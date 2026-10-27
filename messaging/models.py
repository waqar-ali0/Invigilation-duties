"""WhatsApp messaging through the official WhatsApp Business Platform (Cloud API).

Secrets (access token, app secret, webhook verify token) live only in the server's .env; the
database keeps identifiers, template names, consent and the message log."""

from datetime import time

from django.conf import settings
from django.db import models


class Consent(models.TextChoices):
    UNKNOWN = "unknown", "Not asked yet"
    OPTED_IN = "opted_in", "Opted in"
    OPTED_OUT = "opted_out", "Opted out"


class Preference(models.TextChoices):
    BOTH = "both", "WhatsApp and email"
    WHATSAPP = "whatsapp", "WhatsApp only"
    EMAIL = "email", "Email only"


class ContactStatus(models.TextChoices):
    OK = "ok", "OK"
    INVALID = "invalid", "Number not on WhatsApp / invalid"
    BLOCKED = "blocked", "Blocked by the exam office"


class WhatsAppContact(models.Model):
    """A faculty member's WhatsApp details and consent. The number defaults to their phone."""

    faculty = models.OneToOneField("faculty.Faculty", on_delete=models.CASCADE, related_name="whatsapp")
    number = models.CharField(
        "WhatsApp number", max_length=16, blank=True,
        help_text="International form, e.g. +923001234567. Leave empty to use their phone number.",
    )
    verified = models.BooleanField(default=False, help_text="Confirmed to be a WhatsApp number.")
    verified_at = models.DateTimeField(null=True, blank=True)
    consent = models.CharField(max_length=10, choices=Consent.choices, default=Consent.UNKNOWN)
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_source = models.CharField(max_length=60, blank=True, help_text="e.g. faculty portal, signed form, exam office")
    preference = models.CharField(max_length=10, choices=Preference.choices, default=Preference.BOTH)
    status = models.CharField(max_length=10, choices=ContactStatus.choices, default=ContactStatus.OK)
    last_error = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"WhatsApp for {self.faculty.name}"

    @property
    def address(self):
        return self.number or self.faculty.phone_e164

    def problem(self):
        """Why reminders can't go to this person on WhatsApp, or "" when they can."""
        if not self.address:
            return "no valid phone number"
        if self.status == ContactStatus.BLOCKED:
            return "blocked by the exam office"
        if self.status == ContactStatus.INVALID:
            return "number marked not on WhatsApp"
        if self.consent != Consent.OPTED_IN:
            return "has not opted in" if self.consent == Consent.UNKNOWN else "opted out"
        if self.preference == Preference.EMAIL:
            return "prefers email only"
        return ""


class MessageKind(models.TextChoices):
    REMINDER = "reminder", "Tomorrow's duty reminder"
    NEW_DUTY = "new_duty", "New duty assignment"
    CHANGED = "changed", "Duty assignment changed"
    ROOM_CHANGED = "room_changed", "Room or time changed"
    REPLACEMENT = "replacement", "Replacement duty assigned"
    CANCELLED = "cancelled", "Duty cancelled"
    ANNOUNCEMENT = "announcement", "General announcement"


# Parameters a template can use, filled from the real duty: label shown to the office.
PARAMETERS = {
    "name": "Faculty name",
    "date": "Exam date",
    "shift": "Time slot",
    "room": "Room",
    "building": "Building",
    "reporting_time": "Reporting time",
    "course": "Course",
    "period": "Exam period",
    "message": "Announcement text",
}

DEFAULT_TEMPLATES = {
    MessageKind.REMINDER: ("duty_reminder", ["name", "date", "shift", "room", "building", "reporting_time"]),
    MessageKind.NEW_DUTY: ("duty_assigned", ["name", "date", "shift", "room", "building"]),
    MessageKind.CHANGED: ("duty_changed", ["name", "date", "shift", "room", "building"]),
    MessageKind.ROOM_CHANGED: ("duty_room_changed", ["name", "date", "shift", "room", "building"]),
    MessageKind.REPLACEMENT: ("replacement_duty", ["name", "date", "shift", "room", "building"]),
    MessageKind.CANCELLED: ("duty_cancelled", ["name", "date", "shift", "room"]),
    MessageKind.ANNOUNCEMENT: ("exam_announcement", ["name", "message"]),
}


class MessageTemplate(models.Model):
    """Which approved WhatsApp template to use for each kind of message, and in which order its
    {{1}}, {{2}}… body parameters are filled."""

    kind = models.CharField(max_length=15, choices=MessageKind.choices, unique=True)
    name = models.CharField("approved template name", max_length=100)
    language = models.CharField(max_length=10, default="en", help_text="Language code of the approved template, e.g. en, en_US, ur.")
    parameters = models.JSONField(default=list, help_text="Parameter keys in the order of the template's placeholders")
    active = models.BooleanField(default=False, help_text="Only active templates are used.")

    class Meta:
        ordering = ["kind"]

    def __str__(self):
        return f"{self.get_kind_display()} → {self.name} ({self.language})"


class WhatsAppSettings(models.Model):
    """One row: identifiers and reminder schedule. The access token is never stored here."""

    enabled = models.BooleanField(default=False, help_text="Send automatic reminders and change notices.")
    api_version = models.CharField(max_length=10, default="v21.0")
    phone_number_id = models.CharField("WhatsApp Business phone number ID", max_length=40, blank=True)
    business_account_id = models.CharField("WhatsApp Business Account ID", max_length=40, blank=True)
    days_ahead = models.PositiveIntegerField(default=1, help_text="Remind this many days before the duty (1 = the day before).")
    dispatch_time = models.TimeField(default=time(18, 0), help_text="Local time the daily reminders are queued.")
    report_minutes_before = models.PositiveIntegerField(
        null=True, blank=True, help_text="Reporting time = exam start minus this many minutes. Empty = not shown."
    )
    remind_replacements = models.BooleanField(default=True, help_text="Also remind people holding replacement, stand-in or make-up duties.")
    notify_changes = models.BooleanField(default=True, help_text="Send a notice when a duty is given, moved, replaced or cancelled.")
    only_published = models.BooleanField(default=True, help_text="Only for published rosters.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "WhatsApp settings"

    def __str__(self):
        return "WhatsApp settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def token(self):
        return getattr(settings, "WHATSAPP_TOKEN", "")

    @property
    def configured(self):
        return bool(self.token and self.phone_number_id)

    def missing(self):
        out = []
        if not self.token:
            out.append("WHATSAPP_TOKEN in the server's .env")
        if not self.phone_number_id:
            out.append("the phone number ID")
        if not MessageTemplate.objects.filter(kind=MessageKind.REMINDER, active=True).exists():
            out.append("an active reminder template")
        return out


class MessageStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Being submitted"
    SUBMITTED = "submitted", "Submitted to WhatsApp"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    READ = "read", "Read"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


# Later statuses never go back to earlier ones (webhooks can arrive out of order).
STATUS_RANK = {MessageStatus.SUBMITTED: 1, MessageStatus.SENT: 2, MessageStatus.DELIVERED: 3, MessageStatus.READ: 4}


class WhatsAppMessage(models.Model):
    key = models.CharField(max_length=120, unique=True, null=True, blank=True, help_text="Duty + kind + occurrence; prevents duplicates.")
    kind = models.CharField(max_length=15, choices=MessageKind.choices)
    faculty = models.ForeignKey("faculty.Faculty", null=True, blank=True, on_delete=models.SET_NULL, related_name="whatsapp_messages")
    duty = models.ForeignKey("duties.Duty", null=True, blank=True, on_delete=models.SET_NULL, related_name="whatsapp_messages")
    to = models.CharField(max_length=16)
    template_name = models.CharField(max_length=100)
    language = models.CharField(max_length=10)
    params = models.JSONField(default=list)
    preview = models.TextField(blank=True, help_text="The message as the recipient will read it.")
    status = models.CharField(max_length=10, choices=MessageStatus.choices, default=MessageStatus.QUEUED)
    provider_id = models.CharField(max_length=120, blank=True, db_index=True)
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    scheduled_for = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "scheduled_for"])]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.to} ({self.status})"
