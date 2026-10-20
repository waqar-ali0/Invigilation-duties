"""The only way the application sends email: queue it here, send it later.

Sending happens in `send_queued`, run by the `send_emails` command (scheduled every
few minutes) or the admin's "Send queued emails now" button, so no page ever waits
on a mail server.
"""

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .models import EmailStatus, OutboundEmail

MAX_ATTEMPTS = 3


def queue_email(to, subject, body, kind, attachment=None):
    """Queue an email. `attachment` is (filename, content bytes, mimetype) or None."""
    name, content, mimetype = attachment or ("", None, "")
    return OutboundEmail.objects.create(
        to=to,
        subject=subject[:255],
        body=body,
        kind=kind,
        attachment_name=name,
        attachment_content=content,
        attachment_mimetype=mimetype,
    )


def _message(email, connection):
    message = EmailMessage(email.subject, email.body, settings.DEFAULT_FROM_EMAIL, [email.to], connection=connection)
    if email.attachment_name:
        message.attach(email.attachment_name, bytes(email.attachment_content), email.attachment_mimetype)
    return message


def send_queued(limit=None):
    """Send queued emails, oldest first. Returns (sent, newly failed)."""
    queued = OutboundEmail.objects.filter(status=EmailStatus.QUEUED).order_by("created_at", "id")
    if limit is not None:
        queued = queued[:limit]
    sent = failed = 0
    connection = get_connection()
    for email in list(queued):
        try:
            _message(email, connection).send()
        except Exception as exc:  # any delivery problem: record it and retry later
            email.attempts += 1
            email.last_error = str(exc)
            if email.attempts >= MAX_ATTEMPTS:
                email.status = EmailStatus.FAILED
                failed += 1
            email.save(update_fields=["attempts", "last_error", "status"])
        else:
            email.status = EmailStatus.SENT
            email.sent_at = timezone.now()
            email.save(update_fields=["status", "sent_at"])
            sent += 1
    try:
        connection.close()
    except Exception:
        pass
    return sent, failed


def resend(email):
    email.status = EmailStatus.QUEUED
    email.attempts = 0
    email.last_error = ""
    email.save(update_fields=["status", "attempts", "last_error"])
