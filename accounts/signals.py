from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from .models import Role, User

EXAM_OFFICE_GROUP = "Exam Office"

# Apps the exam office manages fully in the Django admin.
FULL_ACCESS_APPS = ("faculty", "exams")
# Individual models the exam office manages fully, in apps that are otherwise view-only.
FULL_ACCESS_MODELS = (("duties", "unavailability"),)
# Apps the exam office may only look at in the Django admin.
VIEW_ONLY_APPS = ("duties", "audit")


def ensure_exam_office_group():
    group, _ = Group.objects.get_or_create(name=EXAM_OFFICE_GROUP)
    perms = Permission.objects.filter(content_type__app_label__in=FULL_ACCESS_APPS) | Permission.objects.filter(
        content_type__app_label__in=VIEW_ONLY_APPS, codename__startswith="view_"
    )
    for app_label, model in FULL_ACCESS_MODELS:
        perms = perms | Permission.objects.filter(content_type__app_label=app_label, content_type__model=model)
    group.permissions.set(perms)
    return group


def _on_post_migrate(sender, **kwargs):
    ensure_exam_office_group()


post_migrate.connect(_on_post_migrate, dispatch_uid="accounts.ensure_exam_office_group")


@receiver(post_save, sender=User, dispatch_uid="accounts.admin_group")
def add_admins_to_exam_office(sender, instance, **kwargs):
    if instance.role == Role.ADMIN:
        instance.groups.add(ensure_exam_office_group())
