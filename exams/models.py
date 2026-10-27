import math
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from faculty.models import Department, Faculty, FacultyCategory


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

    @property
    def availability_is_open(self):
        """Faculty can submit unavailability requests until the deadline."""
        from django.utils import timezone

        return (
            self.status == PeriodStatus.AVAILABILITY_OPEN
            and self.availability_deadline is not None
            and timezone.now() < self.availability_deadline
        )

    @classmethod
    def lock_for_edit(cls, pk):
        """Lock the period row and check it is still editable. Call inside transaction.atomic().

        Every change to a draft goes through this, so changes to one period happen one at a time
        and never act on a status that changed after the page was loaded.
        """
        period = cls.objects.select_for_update().get(pk=pk)
        period.ensure_editable()
        return period

    @classmethod
    def lock_published(cls, pk):
        """Lock the period row for a change to its published roster (swap, replacement, attendance).

        Call inside transaction.atomic(). Closed periods are final.
        """
        period = cls.objects.select_for_update().get(pk=pk)
        if period.status != PeriodStatus.PUBLISHED:
            raise PeriodLockedError("Only a published roster can be changed this way.")
        return period

    def cap_for(self, category):
        """The maximum number of duties for a faculty member in this category (object, id or code) in this period."""
        if isinstance(category, str) and not category.isdigit():
            override = self.designation_caps.filter(category__code=category).first()
        else:
            override = self.designation_caps.filter(category_id=getattr(category, "pk", category)).first()
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
    swap_cutoff_hours = models.PositiveIntegerField(
        default=24, help_text="Faculty can ask for and accept swaps until this many hours before the exam."
    )

    class Meta:
        verbose_name_plural = "period settings"

    def __str__(self):
        return f"Settings for {self.period}"


class DesignationCap(models.Model):
    period = models.ForeignKey(ExamPeriod, on_delete=models.CASCADE, related_name="designation_caps")
    category = models.ForeignKey(FacultyCategory, on_delete=models.PROTECT, related_name="+")
    max_duties = models.PositiveIntegerField()

    class Meta:
        verbose_name = "category cap"
        constraints = [models.UniqueConstraint(fields=["period", "category"], name="unique_category_cap")]

    def __str__(self):
        return f"{self.category}: {self.max_duties}"


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

    @property
    def starts_at(self):
        """When the exam starts, as an aware datetime in the local time zone."""
        from django.utils import timezone

        return timezone.make_aware(datetime.combine(self.date, self.start_time))

    @property
    def ends_at(self):
        from django.utils import timezone

        return timezone.make_aware(datetime.combine(self.date, self.end_time))

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


def room_key(name):
    """Room names compare ignoring case and extra spaces."""
    return " ".join(str(name).split()).lower()


class Building(models.Model):
    name = models.CharField(max_length=100, unique=True)
    campus = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["campus", "name"]

    def __str__(self):
        return f"{self.name} ({self.campus})" if self.campus else self.name


class Room(models.Model):
    """A room in the permanent inventory, reused by every exam period."""

    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="rooms")
    name = models.CharField("room", max_length=50)
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Seats for students.")
    invigilators_needed = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text="Leave empty to work it out from the number of students (period rules).",
    )
    special_requirements = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True, help_text="Inactive rooms are kept for history but not offered for new exams.")

    class Meta:
        ordering = ["building__name", "name"]
        constraints = [models.UniqueConstraint(fields=["building", "name"], name="unique_room_in_building")]

    def __str__(self):
        return f"{self.name} — {self.building.name}"

    def clean(self):
        if self.building_id and self.name:
            clash = Room.objects.filter(building_id=self.building_id).exclude(pk=self.pk)
            if any(room_key(r.name) == room_key(self.name) for r in clash):
                raise ValidationError({"name": "This building already has a room with this name."})


class RoomAllocation(models.Model):
    session = models.ForeignKey(ExamSession, on_delete=models.CASCADE, related_name="rooms")
    room = models.CharField(max_length=50)
    room_ref = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL, related_name="allocations", verbose_name="inventory room"
    )
    student_count = models.PositiveIntegerField()
    invigilators_required = models.PositiveIntegerField()

    class Meta:
        ordering = ["session__slot__date", "session__slot__start_time", "session__course_code", "room"]
        constraints = [models.UniqueConstraint(fields=["session", "room"], name="unique_room_allocation")]

    def __str__(self):
        return f"{self.room} — {self.session}"


def required_invigilators(student_count, settings, room=None):
    """Invigilators a room needs: the inventory room's fixed number when it has one, otherwise one
    per `students_per_invigilator`, never fewer than the minimum."""
    if room is not None and room.invigilators_needed:
        return room.invigilators_needed
    return max(settings.min_invigilators_per_room, math.ceil(student_count / settings.students_per_invigilator))
