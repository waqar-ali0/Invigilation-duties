from django.conf import settings
from django.db import models, transaction
from django.db.models.functions import Lower

from .phones import normalize_phone


class Designation(models.TextChoices):
    """Codes of the categories every installation starts with (see FacultyCategory)."""

    PROFESSOR = "professor", "Professor"
    ASSOCIATE_PROFESSOR = "associate_professor", "Associate Professor"
    ASSISTANT_PROFESSOR = "assistant_professor", "Assistant Professor"
    LECTURER = "lecturer", "Lecturer"
    LAB_ENGINEER = "lab_engineer", "Lab Engineer"
    VISITING = "visiting", "Visiting Faculty"
    BACKUP = "backup", "Backup Faculty"


class FacultyCategory(models.Model):
    """A faculty category (designation). The exam office adds, renames and deactivates these;
    no category carries a fixed number of duties — workload rules are set per exam period."""

    code = models.SlugField(max_length=40, unique=True, help_text="Short fixed identifier; not shown to users.")
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True, help_text="Inactive categories can't be given to new faculty.")
    is_visiting = models.BooleanField(default=False, help_text="Duties can be set per course taught.")
    is_backup = models.BooleanField(default=False, help_text="Members join the backup pool by default.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "faculty categories"

    def __str__(self):
        return self.name

    @classmethod
    def get(cls, code):
        """The category with this code, created from the built-in list if it is missing."""
        category = cls.objects.filter(code=code).first()
        if category is None:
            labels = dict(Designation.choices)
            category = cls.objects.create(
                code=code,
                name=labels.get(code, code.replace("_", " ").title()),
                is_visiting=code == Designation.VISITING,
                is_backup=code == Designation.BACKUP,
                order=list(labels).index(code) if code in labels else 99,
            )
        return category


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    hod = models.ForeignKey(
        "Faculty", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="HOD"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Course(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="courses")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Counter(models.Model):
    """Named counters for identifiers generated on the server (e.g. faculty IDs)."""

    name = models.CharField(max_length=40, primary_key=True)
    value = models.PositiveIntegerField(default=0)


FACULTY_ID_PREFIX = "FAC-"


def next_faculty_id():
    """The next free FAC-0001 style ID. The counter row is locked, so concurrent imports never
    hand out the same ID; IDs already taken (e.g. typed by hand) are skipped."""
    with transaction.atomic():
        counter, _ = Counter.objects.select_for_update().get_or_create(name="faculty_id")
        while True:
            counter.value += 1
            candidate = f"{FACULTY_ID_PREFIX}{counter.value:04d}"
            if not Faculty.objects.filter(employee_id__iexact=candidate).exists():
                counter.save(update_fields=["value"])
                return candidate


class EmploymentStatus(models.TextChoices):
    PERMANENT = "permanent", "Permanent"
    CONTRACT = "contract", "Contract"
    VISITING = "visiting", "Visiting"
    ON_LEAVE = "on_leave", "On leave"
    LEFT = "left", "Left the university"


class FacultyQuerySet(models.QuerySet):
    def eligible(self):
        """Faculty who can be given invigilation duties."""
        return self.filter(is_active=True, is_exempt=False)


class Faculty(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="faculty"
    )
    employee_id = models.CharField(
        "faculty ID", max_length=50, unique=True, blank=True,
        help_text="Leave empty to generate one (FAC-0001). Never changes afterwards.",
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True, null=True, blank=True, help_text="Needed only for an account and emails.")
    phone = models.CharField(max_length=40, blank=True, help_text="As entered, e.g. 0300-1234567.")
    phone_e164 = models.CharField(
        "phone (international)", max_length=16, blank=True, db_index=True, editable=False,
        help_text="Worked out from the phone number, e.g. +923001234567. Empty when the number is not valid.",
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="faculty")
    category = models.ForeignKey(FacultyCategory, on_delete=models.PROTECT, related_name="faculty")
    employment_status = models.CharField(max_length=12, choices=EmploymentStatus.choices, default=EmploymentStatus.PERMANENT)
    courses = models.ManyToManyField(Course, blank=True, related_name="teachers")
    is_active = models.BooleanField(default=True)
    is_exempt = models.BooleanField(default=False, help_text="Exempt from invigilation entirely.")
    remarks = models.TextField(blank=True)
    invited_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    objects = FacultyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "faculty"
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="faculty_email_ci_unique",
                violation_error_message="A faculty member with this email already exists.",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.department})"

    @property
    def phone_invalid(self):
        return bool(self.phone) and not self.phone_e164

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip() or None
        self.phone_e164 = normalize_phone(self.phone) or ""
        if not self.employee_id:
            self.employee_id = next_faculty_id()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "phone" in update_fields:
            kwargs["update_fields"] = list(set(update_fields) | {"phone_e164"})
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        from .signals import email_taken

        if self.user_id and self.email and email_taken(self.email, self.user_id):
            raise ValidationError({"email": "This email is already used by another account."})
        if self.user_id and not (self.email or "").strip():
            raise ValidationError({"email": "This faculty member has an account, so an email is required."})
        if self.category_id and not self.category.is_active and (
            self._state.adding or not Faculty.objects.filter(pk=self.pk, category_id=self.category_id).exists()
        ):
            raise ValidationError({"category": "This category is no longer in use."})
