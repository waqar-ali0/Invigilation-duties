"""The only way the application sends email: queue it here, send it later.

Sending happens in `send_queued`, run by the `send_emails` command (scheduled every
few minutes) or the admin's "Send queued emails now" button, so no page ever waits
on a mail server.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .models import EmailKind, EmailStatus, OutboundEmail

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=30)
LINK = re.compile(r"https?://\S+")


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


def _redact(email):
    """Links in account emails are one-time secrets: don't keep them once sent."""
    if email.kind in (EmailKind.ACTIVATION, EmailKind.PASSWORD_RESET):
        email.body = LINK.sub("[link removed after sending]", email.body)


def _claim(email_id):
    """Take a queued email for this run. False if another run already took it."""
    return bool(
        OutboundEmail.objects.filter(pk=email_id, status=EmailStatus.QUEUED).update(
            status=EmailStatus.SENDING, claimed_at=timezone.now()
        )
    )


def send_queued(limit=None):
    """Send queued emails, oldest first. Returns (sent, newly failed).

    Each email is claimed before sending, so overlapping runs never send one twice.
    Emails left "sending" by a run that crashed are retried after STALE_AFTER.
    """
    OutboundEmail.objects.filter(status=EmailStatus.SENDING, claimed_at__lt=timezone.now() - STALE_AFTER).update(
        status=EmailStatus.QUEUED
    )
    ids = OutboundEmail.objects.filter(status=EmailStatus.QUEUED).order_by("created_at", "id").values_list("id", flat=True)
    if limit is not None:
        ids = ids[:limit]
    sent = failed = 0
    connection = get_connection()
    for email_id in list(ids):
        if not _claim(email_id):
            continue
        email = OutboundEmail.objects.get(pk=email_id)
        try:
            _message(email, connection).send()
        except Exception as exc:  # any delivery problem: record it and retry later
            email.attempts += 1
            email.last_error = str(exc)
            email.status = EmailStatus.FAILED if email.attempts >= MAX_ATTEMPTS else EmailStatus.QUEUED
            failed += email.status == EmailStatus.FAILED
            email.save(update_fields=["attempts", "last_error", "status"])
        else:
            email.status = EmailStatus.SENT
            email.sent_at = timezone.now()
            _redact(email)
            email.save(update_fields=["status", "sent_at", "body"])
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
