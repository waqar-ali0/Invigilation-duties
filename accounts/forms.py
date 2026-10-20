from django.contrib.auth.forms import PasswordResetForm
from django.urls import reverse

from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email


class OutboxPasswordResetForm(PasswordResetForm):
    """Password reset whose email goes through the outbox like every other email."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        link = absolute_url(reverse("password_reset_confirm", args=[context["uid"], context["token"]]))
        subject, body = render_email("password_reset", {"user": context["user"], "link": link, "days": 7})
        queue_email(to_email, subject, body, EmailKind.PASSWORD_RESET)
