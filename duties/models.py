from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from exams.models import ExamPeriod, RoomAllocation, TimeSlot
from faculty.models import Faculty


class UnavailabilityStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Unavailability(models.Model):
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="unavailability")
    period = models.ForeignKey(ExamPeriod, on_delete=models.CASCADE, related_name="unavailability")
    date = models.DateField()
    slot = models.ForeignKey(
        TimeSlot, null=True, blank=True, on_delete=models.CASCADE, help_text="Leave empty for the whole day."
    )
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=UnavailabilityStatus.choices, default=UnavailabilityStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["date"]
        verbose_name_plural = "unavailability"

    def clean(self):
        if self.slot_id is None:
            return
        if self.period_id is not None and self.slot.period_id != self.period_id:
            raise ValidationError({"slot": "This time slot belongs to a different exam period."})
        if self.date is not None and self.slot.date != self.date:
            raise ValidationError({"slot": "This time slot is not on the chosen date."})

    def __str__(self):
        return f"{self.faculty.name} — {self.date}{' ' + str(self.slot.start_time) if self.slot else ''}"


class DutyStatus(models.TextChoices):
    ASSIGNED = "assigned", "Assigned"
    COMPLETED = "completed", "Completed"
    ABSENT = "absent", "Absent"
    CANCELLED = "cancelled", "Cancelled"


class DutySource(models.TextChoices):
    AUTO = "auto", "Auto"
    MANUAL = "manual", "Manual"
    SWAP = "swap", "Swap"
    REPLACEMENT = "replacement", "Replacement"
    STAND_IN = "stand_in", "Stand-in"  # covered for someone marked absent


class DutyQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=DutyStatus.CANCELLED)


class Duty(models.Model):
    period = models.ForeignKey(ExamPeriod, on_delete=models.CASCADE, related_name="duties", editable=False)
    faculty = models.ForeignKey(Faculty, on_delete=models.PROTECT, related_name="duties")
    room_allocation = models.ForeignKey(RoomAllocation, on_delete=models.CASCADE, related_name="duties")
    status = models.CharField(max_length=10, choices=DutyStatus.choices, default=DutyStatus.ASSIGNED)
    source = models.CharField(max_length=12, choices=DutySource.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    # The duty this one took over (by swap or replacement); the old one stays, cancelled.
    replaces = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replaced_by")
    attendance_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    attendance_marked_at = models.DateTimeField(null=True, blank=True)

    objects = DutyQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "duties"
        constraints = [
            models.UniqueConstraint(
                fields=["faculty", "room_allocation"],
                condition=~Q(status="cancelled"),
                name="unique_active_duty",
            )
        ]

    def save(self, *args, **kwargs):
        self.period_id = self.room_allocation.session.slot.period_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.faculty.name} — {self.room_allocation}"


class SwapStatus(models.TextChoices):
    PENDING_PEER = "pending_peer", "Waiting for colleague"
    PENDING_ADMIN = "pending_admin", "Waiting for exam office"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined by colleague"
    REJECTED = "rejected", "Rejected by exam office"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


OPEN_SWAP_STATUSES = (SwapStatus.PENDING_PEER, SwapStatus.PENDING_ADMIN)


class SwapRequest(models.Model):
    """A faculty member asking a colleague to take over one of their duties."""

    duty = models.ForeignKey(Duty, on_delete=models.CASCADE, related_name="swap_requests")
    requested_by = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="swap_requests")
    requested_with = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name="swap_invitations")
    message = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=SwapStatus.choices, default=SwapStatus.PENDING_PEER)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    comment = models.TextField(blank=True)
    new_duty = models.ForeignKey(Duty, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["duty"], condition=Q(status__in=OPEN_SWAP_STATUSES), name="one_open_swap_per_duty"
            )
        ]

    @property
    def is_open(self):
        return self.status in OPEN_SWAP_STATUSES

    def __str__(self):
        return f"{self.requested_by.name} → {self.requested_with.name}: {self.duty.room_allocation}"
