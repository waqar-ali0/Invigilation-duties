from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from .managers import UserManager


class Role(models.TextChoices):
    ADMIN = "admin", "Admin (exam office)"
    EXAM_STAFF = "exam_staff", "Exam staff"
    HOD = "hod", "Head of department"
    FACULTY = "faculty", "Faculty"


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.FACULTY)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="user_email_ci_unique",
                violation_error_message="A user with this email already exists.",
            )
        ]

    def save(self, *args, **kwargs):
        # The exam office works in the Django admin, so admins must be staff.
        if self.role == Role.ADMIN:
            self.is_staff = True
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class HodAccess(models.Model):
    """What heads of department may see and do for their own department(s). One row, set by the
    exam office; checked on the server for every HOD page."""

    see_workload = models.BooleanField("see workload per person", default=True)
    see_missed = models.BooleanField("see missed duties", default=True)
    decide_absences = models.BooleanField("record whether an absence is excused", default=False)
    see_contacts = models.BooleanField("see phone numbers and emails", default=False)
    download_reports = models.BooleanField("open and download department reports", default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "HOD permissions"
        verbose_name_plural = "HOD permissions"

    def __str__(self):
        return "HOD permissions"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
