from django.db import models


class EmailStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class EmailKind(models.TextChoices):
    ACTIVATION = "activation", "Account activation"
    PASSWORD_RESET = "password_reset", "Password reset"
    AVAILABILITY_OPEN = "availability_open", "Availability window opened"
    REQUEST_DECISION = "request_decision", "Request decision"
    ROSTER_PUBLISHED = "roster_published", "Roster published"
    REMINDER = "reminder", "Duty reminder"
    SWAP_REQUESTED = "swap_requested", "Swap requested"
    SWAP_ACCEPTED = "swap_accepted", "Swap accepted"
    SWAP_DECLINED = "swap_declined", "Swap declined"
    SWAP_APPROVED = "swap_approved", "Swap approved"
    SWAP_REJECTED = "swap_rejected", "Swap rejected"
    SWAP_CANCELLED = "swap_cancelled", "Swap cancelled"
    REPLACED_OUT = "replaced_out", "Replaced (duty removed)"
    REPLACED_IN = "replaced_in", "Replaced (duty given)"


class OutboundEmail(models.Model):
    """An email waiting to be sent, or the record of one that was sent or failed."""

    to = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    kind = models.CharField(max_length=30, choices=EmailKind.choices)
    status = models.CharField(max_length=10, choices=EmailStatus.choices, default=EmailStatus.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)  # when a send run took it
    attachment_name = models.CharField(max_length=200, blank=True)
    attachment_content = models.BinaryField(null=True, blank=True)
    attachment_mimetype = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.to} ({self.status})"
