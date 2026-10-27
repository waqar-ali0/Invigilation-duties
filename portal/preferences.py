"""Soft preferences: faculty say which dates, slots or buildings suit them (or which slot they'd
rather avoid). They never block a duty; the engine follows them when it fairly can."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, role_required
from audit.log import log
from duties.models import Preference, PreferenceKind
from exams.models import Building, ExamPeriod, PeriodStatus, TimeSlot
from faculty.models import Faculty

admin_only = role_required(Role.ADMIN)
MAX_PER_PERIOD = 10


class PreferenceForm(forms.ModelForm):
    class Meta:
        model = Preference
        fields = ["kind", "date", "slot", "building", "remarks"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
        labels = {"kind": "Preference", "slot": "Time slot", "remarks": "Remarks (optional)"}

    def __init__(self, *args, period=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["slot"].queryset = TimeSlot.objects.filter(period=period)
        self.fields["building"].queryset = Building.objects.filter(is_active=True)
        self.instance.period = period

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind == PreferenceKind.DATE:
            day = data.get("date")
            if not day:
                self.add_error("date", "Choose a date.")
            elif not (self.period.start_date <= day <= self.period.end_date):
                self.add_error("date", "Choose a date within the exam period.")
            data["slot"] = data["building"] = None
        elif kind in (PreferenceKind.SLOT, PreferenceKind.AVOID_SLOT):
            if not data.get("slot"):
                self.add_error("slot", "Choose a time slot.")
            data["date"] = data["building"] = None
        elif kind == PreferenceKind.BUILDING:
            if not data.get("building"):
                self.add_error("building", "Choose a building.")
            data["date"] = data["slot"] = None
        for name in ("date", "slot", "building"):
            setattr(self.instance, name, data.get(name))
        return data


def _open_periods():
    return [p for p in ExamPeriod.objects.filter(status=PeriodStatus.AVAILABILITY_OPEN) if p.availability_is_open]


@faculty_required
def my_preferences(request):
    faculty = request.faculty
    periods = _open_periods()
    forms_by_period = []
    for period in periods:
        bound = request.method == "POST" and request.POST.get("period") == str(period.pk)
        form = PreferenceForm(request.POST if bound else None, period=period, prefix=f"p{period.pk}")
        if bound and form.is_valid():
            if Preference.objects.filter(faculty=faculty, period=period).count() >= MAX_PER_PERIOD:
                messages.error(request, f"You can give at most {MAX_PER_PERIOD} preferences per exam period.")
            else:
                pref = form.save(commit=False)
                pref.faculty, pref.period = faculty, period
                pref.save()
                log(request.user, "preference.add", pref, after={"kind": pref.kind})
                messages.success(request, "Preference saved. It is a wish, not a guarantee.")
                return redirect("my_preferences")
        forms_by_period.append((period, form, Preference.objects.filter(faculty=faculty, period=period).select_related("slot", "building")))
    return render(request, "portal/my_preferences.html", {"forms": forms_by_period, "faculty": faculty})


@faculty_required
@require_POST
def remove_preference(request, pk):
    pref = get_object_or_404(Preference, pk=pk, faculty=request.faculty)
    if pref.period not in _open_periods():
        messages.error(request, "Preferences for this period can no longer be changed.")
    else:
        log(request.user, "preference.remove", pref, before={"kind": pref.kind})
        pref.delete()
        messages.success(request, "Preference removed.")
    return redirect("my_preferences")


class AdminPreferenceForm(PreferenceForm):
    faculty = forms.ModelChoiceField(Faculty.objects.filter(is_active=True).select_related("department").order_by("name"))

    class Meta(PreferenceForm.Meta):
        fields = ["faculty"] + PreferenceForm.Meta.fields


@admin_only
def period_preferences(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    form = AdminPreferenceForm(request.POST or None, period=period)
    if request.method == "POST" and request.POST.get("action") == "delete":
        pref = get_object_or_404(Preference, pk=request.POST.get("id") or 0, period=period)
        log(request.user, "preference.remove", pref, before={"faculty": pref.faculty_id, "kind": pref.kind})
        pref.delete()
        messages.success(request, "Preference removed.")
        return redirect("period_preferences", pk=pk)
    if request.method == "POST" and form.is_valid():
        pref = form.save(commit=False)
        pref.period = period
        pref.save()
        log(request.user, "preference.add", pref, after={"faculty": pref.faculty_id, "kind": pref.kind})
        messages.success(request, f"Preference added for {pref.faculty.name}.")
        return redirect("period_preferences", pk=pk)
    prefs = Preference.objects.filter(period=period).select_related("faculty", "slot", "building").order_by("faculty__name")
    return render(request, "portal/period_preferences.html", {"period": period, "prefs": prefs, "form": form, "last_run": period.last_run or {}})
