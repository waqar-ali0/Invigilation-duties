"""Keep user accounts in step with faculty records and department headship."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from accounts.models import Role

from .models import Department, Faculty


def sync_hod_role(faculty):
    """Faculty and HOD roles follow the department HOD field; other roles are left alone."""
    user = getattr(faculty, "user", None)
    if user is None or user.role not in (Role.FACULTY, Role.HOD):
        return
    role = Role.HOD if Department.objects.filter(hod=faculty).exists() else Role.FACULTY
    if user.role != role:
        user.role = role
        user.save(update_fields=["role"])


@receiver(pre_save, sender=Department, dispatch_uid="faculty.department_old_hod")
def remember_old_hod(sender, instance, **kwargs):
    instance._old_hod_id = (
        Department.objects.filter(pk=instance.pk).values_list("hod_id", flat=True).first() if instance.pk else None
    )


@receiver(post_save, sender=Department, dispatch_uid="faculty.department_hod_roles")
def update_hod_roles(sender, instance, **kwargs):
    ids = {instance.hod_id, getattr(instance, "_old_hod_id", None)} - {None}
    for faculty in Faculty.objects.filter(pk__in=ids).select_related("user"):
        sync_hod_role(faculty)


def is_faculty_account(user):
    """Only plain faculty/HOD accounts follow their faculty record; exam office and staff accounts never do."""
    return user.role in (Role.FACULTY, Role.HOD) and not user.is_staff and not user.is_superuser


def email_taken(email, user_id):
    from accounts.models import User

    return User.objects.filter(email__iexact=email).exclude(pk=user_id).exists()


@receiver(post_save, sender=Faculty, dispatch_uid="faculty.sync_user")
def sync_user(sender, instance, **kwargs):
    user = instance.user
    if user is None or not is_faculty_account(user):
        return
    changed = []
    if user.email != instance.email.lower() and not email_taken(instance.email, user.pk):
        user.email = instance.email.lower()
        changed.append("email")
    if user.is_active != instance.is_active:
        user.is_active = instance.is_active
        changed.append("is_active")
    if changed:
        user.save(update_fields=changed)
