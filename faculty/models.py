from django.conf import settings
from django.db import models


class Designation(models.TextChoices):
    PROFESSOR = "professor", "Professor"
    ASSOCIATE_PROFESSOR = "associate_professor", "Associate Professor"
    ASSISTANT_PROFESSOR = "assistant_professor", "Assistant Professor"
    LECTURER = "lecturer", "Lecturer"
    VISITING = "visiting", "Visiting"


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    hod = models.ForeignKey(
        "Faculty", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="HOD"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FacultyQuerySet(models.QuerySet):
    def eligible(self):
        """Faculty who can be given invigilation duties."""
        return self.filter(is_active=True, is_exempt=False)


class Faculty(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="faculty"
    )
    employee_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="faculty")
    designation = models.CharField(max_length=30, choices=Designation.choices)
    is_active = models.BooleanField(default=True)
    is_exempt = models.BooleanField(default=False, help_text="Exempt from invigilation entirely.")
    invited_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = FacultyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "faculty"

    def __str__(self):
        return f"{self.name} ({self.department})"
