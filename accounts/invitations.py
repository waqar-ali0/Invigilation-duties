"""Inviting faculty to create their account (activation emails)."""

from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from audit.log import log
from faculty.models import Department, Faculty
from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email

from .models import Role, User


class InvitationError(Exception):
    pass


def role_for(faculty):
    return Role.HOD if Department.objects.filter(hod=faculty).exists() else Role.FACULTY


def activation_url(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return absolute_url(reverse("activate", args=[uid, default_token_generator.make_token(user)]))


def invite(faculty, actor):
    """Create (or reuse) the faculty member's account and queue an activation email."""
    if not faculty.is_active:
        raise InvitationError(f"{faculty.name} is inactive.")
    with transaction.atomic():
        user = faculty.user
        if user is not None and user.has_usable_password():
            raise InvitationError(f"{faculty.name} already has an active account.")
        if user is None:
            user = User.objects.filter(email__iexact=faculty.email).first()
            if user is None:
                user = User(email=faculty.email.lower(), role=role_for(faculty))
                user.set_unusable_password()
                user.save()
            faculty.user = user
        faculty.invited_at = timezone.now()
        faculty.save(update_fields=["user", "invited_at"])
        subject, body = render_email(
            "activation", {"faculty": faculty, "link": activation_url(user), "days": 7}
        )
        queue_email(user.email, subject, body, EmailKind.ACTIVATION)
        log(actor, "account.invite", faculty, after={"user": user.pk})
    return user


def invite_all(actor):
    """Invite every active faculty member who has no account and was never invited."""
    count = 0
    for faculty in Faculty.objects.filter(is_active=True, user__isnull=True, invited_at__isnull=True):
        invite(faculty, actor)
        count += 1
    return count
