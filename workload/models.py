"""Workload rules: every duty number is set per exam period by the exam office — none is built in."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TargetKind(models.TextChoices):
    EXACT = "exact", "Exactly"
    MINIMUM = "minimum", "At least"
    MAXIMUM = "maximum", "At most"
    PREFERRED = "preferred", "About (preferred)"


class RuleFields(models.Model):
    """Target, its kind, and optional bounds. Empty means "not set" — never zero."""

    target = models.PositiveIntegerField(null=True, blank=True, help_text="Leave empty if not decided yet.")
    kind = models.CharField(max_length=10, choices=TargetKind.choices, blank=True, help_text="How the target is applied.")
    min_duties = models.PositiveIntegerField("minimum", null=True, blank=True)
    max_duties = models.PositiveIntegerField("maximum", null=True, blank=True)

    class Meta:
        abstract = True

    def clean(self):
        errors = {}
        if self.min_duties is not None and self.max_duties is not None and self.min_duties > self.max_duties:
            errors["max_duties"] = "The maximum can't be below the minimum."
        if self.kind and self.target is None and not getattr(self, "per_course", None):
            errors["target"] = "Give the target this kind applies to."
        if self.target is not None and self.max_duties is not None and self.kind in ("exact", "minimum") and self.target > self.max_duties:
            errors["target"] = "The target is above the maximum."
        if errors:
            raise ValidationError(errors)


class WorkloadPolicy(models.Model):
    """How duties count toward targets in one exam period."""

    period = models.OneToOneField("exams.ExamPeriod", on_delete=models.CASCADE, related_name="workload_policy")
    excused_absence_counts = models.BooleanField(default=True, help_text="An excused absence counts as a duty done.")
    unexcused_absence_counts = models.BooleanField(default=False, help_text="An unexcused absence counts as a duty done.")
    makeup_counts = models.BooleanField(default=True, help_text="A make-up duty for a missed one counts toward the target.")
    stand_in_counts = models.BooleanField(default=True, help_text="Covering for an absent colleague counts toward the coverer's target.")
    combined_periods = models.ManyToManyField(
        "exams.ExamPeriod", blank=True, related_name="+",
        help_text="Duties in these periods count toward the same targets (e.g. midterms and finals of one semester).",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name_plural = "workload policies"

    def __str__(self):
        return f"Workload policy for {self.period}"

    @classmethod
    def for_period(cls, period):
        policy, _ = cls.objects.get_or_create(period=period)
        return policy


class CategoryRule(RuleFields):
    period = models.ForeignKey("exams.ExamPeriod", on_delete=models.CASCADE, related_name="category_rules")
    category = models.ForeignKey("faculty.FacultyCategory", on_delete=models.PROTECT, related_name="+")
    per_course = models.PositiveIntegerField(
        "duties per course", null=True, blank=True,
        help_text="For visiting faculty: target = this × the courses they teach with exams in this period.",
    )

    class Meta:
        ordering = ["category__order", "category__name"]
        constraints = [models.UniqueConstraint(fields=["period", "category"], name="unique_category_rule")]

    def __str__(self):
        return f"{self.category} in {self.period}"


class FacultyRule(RuleFields):
    """A person's own rule for one period: overrides their category, or exempts them."""

    period = models.ForeignKey("exams.ExamPeriod", on_delete=models.CASCADE, related_name="faculty_rules")
    faculty = models.ForeignKey("faculty.Faculty", on_delete=models.CASCADE, related_name="workload_rules")
    exempt = models.BooleanField(default=False, help_text="No duties in this period.")
    allow_inactive = models.BooleanField(
        default=False, help_text="Exception: allow duties although the faculty member is marked inactive."
    )
    reason = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["faculty__name"]
        constraints = [models.UniqueConstraint(fields=["period", "faculty"], name="unique_faculty_rule")]

    def __str__(self):
        return f"{self.faculty.name} in {self.period}"


class WorkloadVersion(models.Model):
    """A snapshot of a period's workload rules, kept when they are confirmed or changed after publishing."""

    period = models.ForeignKey("exams.ExamPeriod", on_delete=models.CASCADE, related_name="workload_versions")
    number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=200)
    snapshot = models.JSONField()

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["period", "number"], name="unique_workload_version")]

    def __str__(self):
        return f"{self.period} rules v{self.number}"
