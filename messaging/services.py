"""Queueing, sending and tracking WhatsApp messages.

Every value in a message comes from the real duty record. A message is never marked sent
unless WhatsApp says so: a successful API call only makes it "submitted"; "sent", "delivered"
and "read" come from WhatsApp's own status reports (webhook)."""

from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.log import log
from duties.models import Duty, DutySource, DutyStatus
from exams.models import PeriodStatus

from . import client
from .models import (
    PARAMETERS,
    STATUS_RANK,
    ContactStatus,
    MessageKind,
    MessageStatus,
    MessageTemplate,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppSettings,
)

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)
RETRY_AFTER = timedelta(minutes=10)


def contact_for(faculty):
    contact, _ = WhatsAppContact.objects.get_or_create(faculty=faculty)
    return contact


def eligibility(faculty):
    """(contact, problem): problem is "" when WhatsApp messages may go to `faculty`."""
    contact = getattr(faculty, "whatsapp", None)
    if contact is None:
        contact = WhatsAppContact(faculty=faculty)
    return contact, contact.problem()


def duty_values(duty, cfg=None):
    """Template values for `duty`, all taken from the database."""
    cfg = cfg or WhatsAppSettings.get()
    slot = duty.room_allocation.session.slot
    room = duty.room_allocation
    reporting = ""
    if cfg.report_minutes_before is not None:
        reporting = (datetime.combine(slot.date, slot.start_time) - timedelta(minutes=cfg.report_minutes_before)).strftime("%H:%M")
    return {
        "name": duty.faculty.name,
        "date": slot.date.strftime("%a %d %b %Y"),
        "shift": f"{slot.start_time:%H:%M}–{slot.end_time:%H:%M}",
        "room": room.room,
        "building": room.room_ref.building.name if room.room_ref_id else "—",
        "reporting_time": reporting or "—",
        "course": room.session.course_code,
        "period": duty.period.name,
    }


PREVIEW = {
    MessageKind.REMINDER: "Dear {name},\n\nThis is a reminder of your examination duty scheduled for {date}.\n\nDate: {date}\nShift: {shift}\nRoom: {room}\nBuilding: {building}\nReporting Time: {reporting_time}\n\nPlease report on time. If you have any concerns regarding this assignment, please contact the examination administration.\n\nRegards,\nExamination Duty Management System",
    MessageKind.NEW_DUTY: "Dear {name}, you have a new examination duty: {date}, {shift}, room {room} ({building}).",
    MessageKind.CHANGED: "Dear {name}, one of your examination duties has changed. It is now {date}, {shift}, room {room} ({building}).",
    MessageKind.ROOM_CHANGED: "Dear {name}, your examination duty has moved to {date}, {shift}, room {room} ({building}).",
    MessageKind.REPLACEMENT: "Dear {name}, you have been assigned a replacement duty: {date}, {shift}, room {room} ({building}).",
    MessageKind.CANCELLED: "Dear {name}, your examination duty on {date}, {shift}, room {room} has been cancelled.",
    MessageKind.ANNOUNCEMENT: "Dear {name},\n\n{message}\n\nExamination administration",
}


def render(kind, values):
    """(template, params in template order, readable preview) or (None, [], "") without an active template."""
    template = MessageTemplate.objects.filter(kind=kind, active=True).first()
    preview = PREVIEW[kind].format(**{k: values.get(k, "—") for k in PARAMETERS})
    if template is None:
        return None, [], preview
    params = [str(values.get(key, "—")) for key in template.parameters]
    return template, params, preview


def _queue(kind, faculty, values, key=None, duty=None, when=None, actor=None):
    """Queue one message. Returns (message or None, reason). Never queues twice for the same key."""
    contact, problem = eligibility(faculty)
    if problem:
        return None, problem
    template, params, preview = render(kind, values)
    if template is None:
        return None, "no active template for this kind of message"
    if key and WhatsAppMessage.objects.filter(key=key).exists():
        return None, "already queued"
    try:
        with transaction.atomic():
            message = WhatsAppMessage.objects.create(
                key=key, kind=kind, faculty=faculty, duty=duty, to=contact.address, template_name=template.name,
                language=template.language, params=params, preview=preview, status=MessageStatus.QUEUED,
                scheduled_for=when or timezone.now(), created_by=actor,
            )
    except IntegrityError:  # another scheduler run queued the same key at the same moment
        return None, "already queued"
    return message, ""


def reminder_duties(day, cfg=None):
    cfg = cfg or WhatsAppSettings.get()
    duties = Duty.objects.filter(room_allocation__session__slot__date=day, status=DutyStatus.ASSIGNED).select_related(
        "faculty__whatsapp", "period", "room_allocation__session__slot", "room_allocation__room_ref__building"
    )
    if cfg.only_published:
        duties = duties.filter(period__status=PeriodStatus.PUBLISHED)
    if not cfg.remind_replacements:
        duties = duties.exclude(source__in=[DutySource.REPLACEMENT, DutySource.STAND_IN, DutySource.MAKEUP])
    return duties.order_by("room_allocation__session__slot__start_time", "faculty__name")


def queue_reminders(day, actor=None):
    """Queue one reminder per qualifying duty on `day`. Safe to run any number of times."""
    cfg = WhatsAppSettings.get()
    queued, skipped = 0, []
    for duty in reminder_duties(day, cfg):
        message, why = _queue(MessageKind.REMINDER, duty.faculty, duty_values(duty, cfg), key=f"reminder:{duty.pk}:{day.isoformat()}", duty=duty, actor=actor)
        if message:
            queued += 1
        elif why != "already queued":
            skipped.append((duty, why))
    return queued, skipped


def notify_duty(duty, kind, actor=None):
    """A change notice for `duty` (new, moved, replacement, cancelled), if enabled. Keyed by duty and kind."""
    cfg = WhatsAppSettings.get()
    if not cfg.notify_changes or (cfg.only_published and duty.period.status != PeriodStatus.PUBLISHED):
        return None
    message, _ = _queue(kind, duty.faculty, duty_values(duty, cfg), key=f"{kind}:{duty.pk}", duty=duty, actor=actor)
    return message


def queue_announcement(faculty_list, text, actor):
    """Queue an announcement to each chosen person; returns (queued, [(faculty, reason)])."""
    text = text.strip()
    queued, skipped = 0, []
    stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    for f in faculty_list:
        message, why = _queue(MessageKind.ANNOUNCEMENT, f, {"name": f.name, "message": text}, key=f"announcement:{stamp}:{f.pk}", actor=actor)
        if message:
            queued += 1
        else:
            skipped.append((f, why))
    log(actor, "whatsapp.announcement", actor, after={"queued": queued, "skipped": len(skipped)})
    return queued, skipped


def due_for_dispatch(now=None):
    """The reminder day to queue now, or None before today's dispatch time."""
    cfg = WhatsAppSettings.get()
    now = timezone.localtime(now or timezone.now())
    if now.time() < cfg.dispatch_time:
        return None
    return now.date() + timedelta(days=cfg.days_ahead)


def _claim(message_id):
    stale = timezone.now() - STALE_AFTER
    return WhatsAppMessage.objects.filter(
        Q(status=MessageStatus.QUEUED) | Q(status=MessageStatus.SENDING, claimed_at__lt=stale), pk=message_id
    ).update(status=MessageStatus.SENDING, claimed_at=timezone.now()) == 1


def send_pending(limit=200):
    """Submit queued messages that are due. Returns (submitted, failed, note)."""
    cfg = WhatsAppSettings.get()
    if not cfg.configured:
        return 0, 0, "WhatsApp is not set up: " + ", ".join(cfg.missing()) + ". Nothing was sent."
    now = timezone.now()
    ids = list(
        WhatsAppMessage.objects.filter(Q(status=MessageStatus.QUEUED) | Q(status=MessageStatus.SENDING, claimed_at__lt=now - STALE_AFTER), scheduled_for__lte=now)
        .order_by("scheduled_for", "id").values_list("id", flat=True)[:limit]
    )
    submitted = failed = 0
    for message_id in ids:
        if not _claim(message_id):
            continue
        message = WhatsAppMessage.objects.get(pk=message_id)
        message.attempts += 1
        try:
            message.provider_id = client.send_template(cfg, message.to, message.template_name, message.language, message.params)
        except client.ApiError as e:
            message.error = str(e)[:500]
            if e.retry and message.attempts < MAX_ATTEMPTS:
                message.status = MessageStatus.QUEUED
                message.scheduled_for = timezone.now() + RETRY_AFTER * message.attempts
            else:
                message.status = MessageStatus.FAILED
                failed += 1
                if e.code in (131026, 131021) and message.faculty_id:
                    WhatsAppContact.objects.filter(faculty_id=message.faculty_id).update(status=ContactStatus.INVALID, last_error=message.error[:300])
        else:
            message.status = MessageStatus.SUBMITTED
            message.submitted_at = timezone.now()
            message.error = ""
            submitted += 1
        message.save()
    return submitted, failed, ""


def retry(message, actor):
    if message.status != MessageStatus.FAILED:
        return False
    message.status, message.scheduled_for, message.error = MessageStatus.QUEUED, timezone.now(), ""
    message.save(update_fields=["status", "scheduled_for", "error", "updated_at"])
    log(actor, "whatsapp.retry", message, after={"to": message.to})
    return True


def cancel(message, actor):
    if message.status not in (MessageStatus.QUEUED, MessageStatus.SCHEDULED):
        return False
    message.status = MessageStatus.CANCELLED
    message.save(update_fields=["status", "updated_at"])
    log(actor, "whatsapp.cancel", message, after={"to": message.to})
    return True


def cancel_for_duty(duty):
    """A duty that is cancelled must not be reminded about: cancel its unsent reminders."""
    WhatsAppMessage.objects.filter(duty=duty, kind=MessageKind.REMINDER, status__in=[MessageStatus.QUEUED, MessageStatus.SCHEDULED]).update(
        status=MessageStatus.CANCELLED
    )


def apply_status_report(payload):
    """Apply WhatsApp webhook status updates. Returns how many messages changed."""
    changed = 0
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            for st in (change.get("value") or {}).get("statuses", []) or []:
                message = WhatsAppMessage.objects.filter(provider_id=st.get("id", "")).first()
                if message is None:
                    continue
                status = st.get("status")
                when = timezone.now()
                try:
                    when = datetime.fromtimestamp(int(st.get("timestamp")), tz=timezone.get_current_timezone())
                except (TypeError, ValueError):
                    pass
                if status == "failed":
                    errors = st.get("errors") or [{}]
                    message.status = MessageStatus.FAILED
                    message.error = (errors[0].get("title") or errors[0].get("message") or "Failed")[:500]
                elif status in ("sent", "delivered", "read"):
                    new = MessageStatus(status)
                    if STATUS_RANK.get(new, 0) <= STATUS_RANK.get(message.status, 0):
                        continue
                    message.status = new
                    setattr(message, f"{status}_at", when)
                    if status in ("delivered", "read") and message.faculty_id:
                        WhatsAppContact.objects.filter(faculty_id=message.faculty_id, verified=False).update(verified=True, verified_at=when)
                else:
                    continue
                message.save()
                changed += 1
    return changed


def on_handover(old, new, kind=MessageKind.REPLACEMENT, actor=None):
    """Notices when `old` passed to someone else as `new` (replacement, swap, move)."""
    cancel_for_duty(old)
    notify_duty(new, kind, actor)
    if new.faculty_id != old.faculty_id:
        notify_duty(old, MessageKind.CANCELLED, actor)


def on_publish(period, actor=None):
    """A "new duty" notice for every duty of a just-published roster (opted-in people only)."""
    cfg = WhatsAppSettings.get()
    if not cfg.notify_changes or not MessageTemplate.objects.filter(kind=MessageKind.NEW_DUTY, active=True).exists():
        return
    opted_in = WhatsAppContact.objects.filter(consent="opted_in").values_list("faculty_id", flat=True)
    for duty in Duty.objects.active().filter(period=period, faculty_id__in=list(opted_in)).select_related(
        "faculty__whatsapp", "period", "room_allocation__session__slot", "room_allocation__room_ref__building"
    ):
        notify_duty(duty, MessageKind.NEW_DUTY, actor)
