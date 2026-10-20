import math

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from faculty.models import Department, Designation, Faculty


class PeriodStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    AVAILABILITY_OPEN = "availability_open", "Availability open"
    ALLOCATING = "allocating", "Allocating"
    PUBLISHED = "published", "Published"
    CLOSED = "closed", "Closed"


class PeriodLockedError(Exception):
    """Raised when something tries to change an exam period that is published or closed."""


class ExamPeriod(models.Model):
    name = models.CharField(max_length=150, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=PeriodStatus.choices, default=PeriodStatus.DRAFT)
    availability_deadline = models.DateTimeField(null=True, blank=True)
    allocation_seed = models.IntegerField(null=True, blank=True, editable=False)
    last_run = models.JSONField(null=True, blank=True, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after the start date."})

    def save(self, *args, **kwargs):
        creating = self._state.adding
        super().save(*args, **kwargs)
        if creating:
            PeriodSettings.objects.get_or_create(period=self)

    def ensure_editable(self):
        if self.status == PeriodStatus.PUBLISHED:
            raise PeriodLockedError("This exam period is published; changes go through swaps and replacements.")
        if self.status == PeriodStatus.CLOSED:
            raise PeriodLockedError("This exam period is closed.")

    def cap_for(self, designation):
        """The maximum number of duties for a faculty member with this designation in this period."""
        override = self.designation_caps.filter(designation=designation).first()
        return override.max_duties if override else self.settings.max_duties_per_faculty


def _positive(default):
    return models.PositiveIntegerField(default=default, validators=[MinValueValidator(1)])


class PeriodSettings(models.Model):
    period = models.OneToOneField(ExamPeriod, on_delete=models.CASCADE, related_name="settings")
    max_duties_per_faculty = _positive(8)
    max_duties_per_day = _positive(2)
    students_per_invigilator = _positive(30)
    min_invigilators_per_room = _positive(2)
    avoid_back_to_back = models.BooleanField(default=True, help_text="Soft rule: avoid consecutive slots on a day.")
    exclude_course_teacher = models.BooleanField(
        default=True, help_text="Soft rule: avoid assigning a teacher to their own course's exam."
    )

    class Meta:
        verbose_name_plural = "period settings"

    def __str__(self):
        return f"Settings for {self.period}"


class DesignationCap(models.Model):
    period = models.ForeignKey(ExamPeriod, on_delete=models.CASCADE, related_name="designation_caps")
    designation = models.CharField(max_length=30, choices=Designation.choices)
    max_duties = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["period", "designation"], name="unique_designation_cap")]

    def __str__(self):
        return f"{self.get_designation_display()}: {self.max_duties}"


class TimeSlot(models.Model):
    period = models.ForeignKey(ExamPeriod, on_delete=models.CASCADE, related_name="slots")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [models.UniqueConstraint(fields=["period", "date", "start_time"], name="unique_slot")]

    def __str__(self):
        return f"{self.date:%a %d %b %Y} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def clean(self):
        errors = {}
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "End time must be after start time."
        if self.date and self.period_id:
            p = self.period
            if not (p.start_date <= self.date <= p.end_date):
                errors["date"] = "Date is outside the exam period."
        if errors:
            raise ValidationError(errors)


class ExamSession(models.Model):
    slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="sessions")
    course_code = models.CharField(max_length=30)
    course_name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="exam_sessions")
    course_teachers = models.ManyToManyField(Faculty, blank=True, related_name="taught_sessions")

    class Meta:
        ordering = ["slot__date", "slot__start_time", "course_code"]
        constraints = [models.UniqueConstraint(fields=["slot", "course_code"], name="unique_session")]

    def __str__(self):
        return f"{self.course_code} — {self.slot}"


class RoomAllocation(models.Model):
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="rooms")
    room = models.CharField(max_length=50)
    student_count = models.PositiveIntegerField()
    invigilators_required = models.PositiveIntegerField()

    class Meta:
        ordering = ["session__slot__date", "session__slot__start_time", "session__course_code", "room"]
        constraints = [models.UniqueConstraint(fields=["session", "room"], name="unique_room_allocation")]

    def __str__(self):
        return f"{self.room} — {self.session}"


def required_invigilators(student_count, settings):
    """Invigilators a room needs: one per `students_per_invigilator`, never fewer than the minimum."""
    return max(settings.min_invigilators_per_room, math.ceil(student_count / settings.students_per_invigilator))
