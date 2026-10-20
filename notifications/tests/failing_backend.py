import smtplib

from django.core.mail.backends.base import BaseEmailBackend


class FailingBackend(BaseEmailBackend):
    """An email backend whose every send fails, for testing retries."""

    def send_messages(self, email_messages):
        raise smtplib.SMTPException("boom")
