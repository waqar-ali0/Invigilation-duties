from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required

from .models import EmailStatus, OutboundEmail
from .outbox import resend, send_queued

admin_only = role_required(Role.ADMIN)
SEND_NOW_LIMIT = 50  # the page waits on the mail server, so send a batch per click and leave the rest queued


@admin_only
def email_list(request):
    counts = dict(OutboundEmail.objects.values_list("status").annotate(n=Count("id")))
    context = {
        "queued": counts.get(EmailStatus.QUEUED, 0),
        "sent": counts.get(EmailStatus.SENT, 0),
        "failed_count": counts.get(EmailStatus.FAILED, 0),
        "failed": OutboundEmail.objects.filter(status=EmailStatus.FAILED).order_by("-created_at")[:200],
        "recent": OutboundEmail.objects.exclude(status=EmailStatus.FAILED).order_by("-created_at")[:50],
    }
    return render(request, "notifications/email_list.html", context)


@admin_only
@require_POST
def send_emails_now(request):
    sent, failed = send_queued(limit=SEND_NOW_LIMIT)
    remaining = OutboundEmail.objects.filter(status=EmailStatus.QUEUED).count()
    messages.success(
        request,
        f"Sent {sent} email(s).{f' {failed} failed.' if failed else ''}"
        f"{f' {remaining} still queued — send again or wait for the scheduled run.' if remaining else ''}",
    )
    return redirect("email_list")


@admin_only
@require_POST
def resend_email(request, pk):
    email = get_object_or_404(OutboundEmail, pk=pk)
    resend(email)
    messages.success(request, f"Email to {email.to} queued again.")
    return redirect("email_list")
