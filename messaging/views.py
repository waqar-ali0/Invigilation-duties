import csv
import io
import json
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, role_required
from audit.log import log
from duties.models import Duty
from exams.models import ExamPeriod
from faculty.models import Department, Faculty
from faculty.phones import normalize_phone

from . import client
from .models import (
    PARAMETERS,
    Consent,
    ContactStatus,
    MessageKind,
    MessageStatus,
    MessageTemplate,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppSettings,
)
from .services import (
    contact_for,
    duty_values,
    eligibility,
    queue_announcement,
    queue_reminders,
    reminder_duties,
    render as render_message,
    retry,
    cancel,
    send_pending,
    apply_status_report,
)

admin_only = role_required(Role.ADMIN)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = WhatsAppSettings
        fields = ["enabled", "phone_number_id", "business_account_id", "api_version", "days_ahead", "dispatch_time",
                  "report_minutes_before", "remind_replacements", "notify_changes", "only_published"]
        widgets = {"dispatch_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}
        labels = {"enabled": "Send automatic reminders and notices"}


class TemplateForm(forms.ModelForm):
    parameters_text = forms.CharField(label="Parameters in the template's order (1st, 2nd, …)", help_text="Comma-separated keys: " + ", ".join(PARAMETERS))

    class Meta:
        model = MessageTemplate
        fields = ["name", "language", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parameters_text"].initial = ", ".join(self.instance.parameters or [])

    def clean_parameters_text(self):
        keys = [k.strip() for k in self.cleaned_data["parameters_text"].split(",") if k.strip()]
        unknown = [k for k in keys if k not in PARAMETERS]
        if unknown:
            raise forms.ValidationError("Unknown: " + ", ".join(unknown))
        return keys

    def save(self, commit=True):
        self.instance.parameters = self.cleaned_data["parameters_text"]
        return super().save(commit)


@admin_only
def settings_page(request):
    cfg = WhatsAppSettings.get()
    action = request.POST.get("action") if request.method == "POST" else None
    form = SettingsForm(request.POST if action == "settings" else None, instance=cfg)
    if action == "settings" and form.is_valid():
        form.save()
        log(request.user, "whatsapp.settings", cfg, after={"enabled": cfg.enabled, "dispatch": str(cfg.dispatch_time)})
        messages.success(request, "WhatsApp settings saved.")
        return redirect("whatsapp_settings")
    if action == "test_connection":
        if not cfg.configured:
            messages.error(request, "Not set up: " + ", ".join(cfg.missing()) + ".")
        else:
            try:
                info = client.check_connection(cfg)
            except client.ApiError as e:
                messages.error(request, f"Connection failed: {e}")
            else:
                messages.success(request, f"Connected: {info.get('verified_name', '?')} ({info.get('display_phone_number', '?')}), quality {info.get('quality_rating', '?')}.")
        return redirect("whatsapp_settings")
    if action == "test_message":
        number = normalize_phone(request.POST.get("number", ""))
        kind = request.POST.get("kind", MessageKind.REMINDER)
        if not number:
            messages.error(request, "Enter a valid phone number for the test.")
        elif not cfg.configured:
            messages.error(request, "Not set up: " + ", ".join(cfg.missing()) + ". Nothing was sent.")
        else:
            sample = {"name": "Test Recipient", "date": timezone.localdate().strftime("%a %d %b %Y"), "shift": "09:00–12:00",
                      "room": "R01", "building": "Main Building", "reporting_time": "08:45", "course": "TEST101",
                      "period": "Test", "message": "This is a test message from the examination duty system."}
            template, params, _ = render_message(kind, sample)
            if template is None:
                messages.error(request, "That message kind has no active template.")
            else:
                try:
                    wamid = client.send_template(cfg, number, template.name, template.language, params)
                except client.ApiError as e:
                    messages.error(request, f"WhatsApp refused the test: {e}")
                else:
                    log(request.user, "whatsapp.test", cfg, after={"to": number, "template": template.name})
                    messages.success(request, f"Submitted to WhatsApp for {number} (id {wamid[:24]}…). Delivery is confirmed only when WhatsApp reports it.")
        return redirect("whatsapp_settings")
    templates = []
    for kind, label in MessageKind.choices:
        t = MessageTemplate.objects.filter(kind=kind).first()
        values = {k: f"[{v}]" for k, v in PARAMETERS.items()}
        templates.append((kind, label, t, render_message(kind, values)[2]))
    stats = {s: WhatsAppMessage.objects.filter(status=s).count() for s in MessageStatus.values}
    context = {
        "cfg": cfg, "form": form, "templates": templates, "stats": stats,
        "token_set": bool(settings.WHATSAPP_TOKEN), "secret_set": bool(settings.WHATSAPP_APP_SECRET),
        "verify_set": bool(settings.WHATSAPP_VERIFY_TOKEN),
        "webhook_url": request.build_absolute_uri("/whatsapp/webhook/"),
        "kinds": MessageKind.choices,
    }
    return render(request, "messaging/settings.html", context)


@admin_only
def template_edit(request, kind):
    if kind not in MessageKind.values:
        return redirect("whatsapp_settings")
    template = MessageTemplate.objects.filter(kind=kind).first() or MessageTemplate(kind=kind)
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == "POST" and form.is_valid():
        t = form.save()
        log(request.user, "whatsapp.template", t, after={"name": t.name, "active": t.active, "params": t.parameters})
        messages.success(request, f"Template for “{t.get_kind_display()}” saved.")
        return redirect("whatsapp_settings")
    values = {k: f"[{v}]" for k, v in PARAMETERS.items()}
    return render(request, "messaging/template.html", {"form": form, "kind": MessageKind(kind), "preview": render_message(kind, values)[2], "parameters": PARAMETERS})


@admin_only
def message_log(request):
    qs = WhatsAppMessage.objects.select_related("faculty")
    status, kind, q = request.GET.get("status", ""), request.GET.get("kind", ""), request.GET.get("q", "").strip()
    if status in MessageStatus.values:
        qs = qs.filter(status=status)
    if kind in MessageKind.values:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(faculty__name__icontains=q) | qs.filter(to__icontains=q.replace(" ", ""))
    if request.method == "POST":
        m = get_object_or_404(WhatsAppMessage, pk=request.POST.get("id") or 0)
        done = retry(m, request.user) if request.POST.get("action") == "retry" else cancel(m, request.user)
        messages.success(request, "Done.") if done else messages.error(request, "That message can't be changed now.")
        return redirect(request.get_full_path())
    if request.GET.get("format") == "csv":
        out = io.StringIO()
        out.write("﻿")
        w = csv.writer(out)
        w.writerow(["Created", "Kind", "Faculty", "To", "Template", "Status", "Attempts", "Submitted", "Sent", "Delivered", "Read", "Error"])
        for m in qs.order_by("created_at"):
            w.writerow([timezone.localtime(m.created_at).strftime("%Y-%m-%d %H:%M"), m.get_kind_display(), m.faculty.name if m.faculty else "",
                        m.to, m.template_name, m.get_status_display(), m.attempts] +
                       [timezone.localtime(t).strftime("%Y-%m-%d %H:%M") if t else "" for t in (m.submitted_at, m.sent_at, m.delivered_at, m.read_at)] + [m.error])
        response = HttpResponse(out.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="whatsapp-messages.csv"'
        return response
    params = request.GET.copy()
    params.pop("page", None)
    context = {"page": Paginator(qs, 50).get_page(request.GET.get("page")), "statuses": MessageStatus.choices, "kinds": MessageKind.choices,
               "f": request.GET, "querystring": params.urlencode(), "cfg": WhatsAppSettings.get()}
    return render(request, "messaging/log.html", context)


@admin_only
def tomorrow(request):
    cfg = WhatsAppSettings.get()
    try:
        day = timezone.datetime.strptime(request.GET.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        day = timezone.localdate() + timedelta(days=cfg.days_ahead)
    if request.method == "POST" and request.POST.get("action") == "queue":
        queued, skipped = queue_reminders(day, actor=request.user)
        messages.success(request, f"{queued} reminder(s) queued for {day:%a %d %b}; {len(skipped)} could not be. "
                         + ("" if cfg.configured else "WhatsApp is not set up, so they will wait until it is."))
        return redirect(f"{request.path}?date={day.isoformat()}")
    rows = []
    sent = {m.duty_id: m for m in WhatsAppMessage.objects.filter(kind=MessageKind.REMINDER, key__endswith=f":{day.isoformat()}")}
    for duty in reminder_duties(day, cfg):
        contact, problem = eligibility(duty.faculty)
        rows.append({"duty": duty, "contact": contact, "problem": problem, "message": sent.get(duty.pk),
                     "preview": render_message(MessageKind.REMINDER, duty_values(duty, cfg))[2]})
    context = {"day": day, "rows": rows, "cfg": cfg, "eligible": sum(1 for r in rows if not r["problem"]),
               "sample": rows[0]["preview"] if rows else ""}
    return render(request, "messaging/tomorrow.html", context)


@admin_only
def centre(request):
    """Messaging centre: choose recipients, write or pick a message, preview, then confirm."""
    data = request.POST if request.method == "POST" else request.GET
    chosen = Faculty.objects.none()
    mode = data.get("audience", "")
    if mode == "people":
        chosen = Faculty.objects.filter(pk__in=[int(x) for x in data.getlist("faculty") if x.isdigit()])
    elif mode == "department" and data.get("department", "").isdigit():
        chosen = Faculty.objects.filter(department_id=data["department"], is_active=True)
    elif mode == "period" and data.get("period", "").isdigit():
        chosen = Faculty.objects.filter(duties__period_id=data["period"]).exclude(duties__status="cancelled").distinct()
    elif mode == "date" and data.get("date"):
        chosen = Faculty.objects.filter(duties__room_allocation__session__slot__date=data["date"]).exclude(duties__status="cancelled").distinct()
    chosen = list(chosen.select_related("whatsapp", "department").order_by("name"))
    text = data.get("text", "").strip()
    if request.method == "POST" and data.get("confirm") == "yes":
        if not text:
            messages.error(request, "Write the announcement first.")
        elif not chosen:
            messages.error(request, "Choose at least one recipient.")
        else:
            queued, skipped = queue_announcement(chosen, text, request.user)
            cfg = WhatsAppSettings.get()
            if cfg.enabled and cfg.configured:
                send_pending()
            messages.success(request, f"{queued} message(s) queued, {len(skipped)} not possible. Check the message log for each result.")
            return redirect("whatsapp_log")
    preview = []
    for f in chosen:
        contact, problem = eligibility(f)
        preview.append((f, contact, problem))
    context = {
        "mode": mode, "data": data, "text": text, "preview": preview, "eligible": sum(1 for _, _, p in preview if not p),
        "sample": render_message(MessageKind.ANNOUNCEMENT, {"name": chosen[0].name if chosen else "[Faculty name]", "message": text or "[your message]"})[2],
        "departments": Department.objects.all(), "periods": ExamPeriod.objects.order_by("-start_date"),
        "people": Faculty.objects.filter(is_active=True).order_by("name").only("pk", "name", "employee_id"),
        "chosen_ids": {f.pk for f in chosen}, "cfg": WhatsAppSettings.get(),
    }
    return render(request, "messaging/centre.html", context)


class ContactAdminForm(forms.ModelForm):
    class Meta:
        model = WhatsAppContact
        fields = ["number", "verified", "consent", "consent_source", "preference", "status"]

    def clean_number(self):
        raw = self.cleaned_data["number"].strip()
        if not raw:
            return ""
        number = normalize_phone(raw)
        if not number:
            raise forms.ValidationError("Not a valid phone number.")
        return number


@admin_only
def contact_edit(request, faculty_id):
    faculty = get_object_or_404(Faculty, pk=faculty_id)
    contact = contact_for(faculty)
    before = {"consent": contact.consent, "verified": contact.verified}
    form = ContactAdminForm(request.POST or None, instance=contact)
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        now = timezone.now()
        if c.consent != before["consent"]:
            c.consent_at = now
            c.consent_source = c.consent_source or "exam office"
        if c.verified and not before["verified"]:
            c.verified_at = now
        c.save()
        log(request.user, "whatsapp.contact", c, before=before, after={"consent": c.consent, "verified": c.verified})
        messages.success(request, f"WhatsApp details for {faculty.name} saved.")
        return redirect("faculty_detail", pk=faculty.pk)
    return render(request, "messaging/contact.html", {"faculty": faculty, "form": form, "contact": contact})


class MyWhatsAppForm(forms.Form):
    number = forms.CharField(label="My WhatsApp number", max_length=40, required=False, help_text="Leave empty to use the phone number the university has for you.")
    consent = forms.ChoiceField(label="Examination duty reminders on WhatsApp", choices=[(Consent.OPTED_IN, "Yes, send them"), (Consent.OPTED_OUT, "No, don't send them")])
    preference = forms.ChoiceField(label="How I want to hear about duties", choices=WhatsAppContact._meta.get_field("preference").choices)

    def clean_number(self):
        raw = self.cleaned_data["number"].strip()
        if raw and not normalize_phone(raw):
            raise forms.ValidationError("Not a valid phone number.")
        return normalize_phone(raw) or ""


@faculty_required
def my_whatsapp(request):
    contact = contact_for(request.faculty)
    form = MyWhatsAppForm(request.POST or None, initial={
        "number": contact.number, "consent": contact.consent if contact.consent != Consent.UNKNOWN else Consent.OPTED_IN, "preference": contact.preference,
    })
    if request.method == "POST" and form.is_valid():
        before = contact.consent
        contact.number = form.cleaned_data["number"]
        contact.preference = form.cleaned_data["preference"]
        if form.cleaned_data["consent"] != before:
            contact.consent = form.cleaned_data["consent"]
            contact.consent_at = timezone.now()
            contact.consent_source = "faculty portal"
        if contact.status == ContactStatus.INVALID and contact.number:
            contact.status = ContactStatus.OK
        contact.save()
        log(request.user, "whatsapp.consent", contact, after={"consent": contact.consent, "preference": contact.preference})
        messages.success(request, "Saved. You can change this at any time.")
        return redirect("my_whatsapp")
    return render(request, "messaging/my_whatsapp.html", {"form": form, "contact": contact, "faculty": request.faculty})


@csrf_exempt
def webhook(request):
    """WhatsApp delivery reports. GET answers Meta's verification; POST must carry a valid signature."""
    if request.method == "GET":
        token = settings.WHATSAPP_VERIFY_TOKEN
        if token and request.GET.get("hub.mode") == "subscribe" and request.GET.get("hub.verify_token") == token:
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden("Verification failed")
    if request.method != "POST":
        return HttpResponse(status=405)
    if not client.valid_signature(request.body, request.headers.get("X-Hub-Signature-256", "")):
        return HttpResponseForbidden("Bad signature")
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    apply_status_report(payload)
    return HttpResponse("ok", content_type="text/plain")
