"""Opening and closing the window in which faculty request unavailable dates."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit.log import log
from faculty.models import Faculty
from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email

from .models import ExamPeriod, PeriodStatus


class AvailabilityError(Exception):
    pass


def open_availability(period, deadline, actor):
    """Open the window until `deadline` and email faculty. Returns the number of emails queued."""
    if deadline <= timezone.now():
        raise AvailabilityError("The deadline must be in the future.")
    if timezone.localtime(deadline).date() > period.end_date:
        raise AvailabilityError("The deadline must be on or before the last exam day.")
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        period.status = PeriodStatus.AVAILABILITY_OPEN
        period.availability_deadline = deadline
        period.save(update_fields=["status", "availability_deadline"])
        link = absolute_url(reverse("my_unavailability"))
        recipients = Faculty.objects.eligible().filter(user__isnull=False, user__is_active=True).select_related("user")
        count = 0
        for faculty in recipients:
            subject, body = render_email(
                "availability_open", {"faculty": faculty, "period": period, "deadline": deadline, "link": link}
            )
            queue_email(faculty.user.email, subject, body, EmailKind.AVAILABILITY_OPEN)
            count += 1
        log(actor, "availability.open", period, after={"deadline": deadline.isoformat(), "emails": count})
    return count


def close_availability(period, actor):
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        period.availability_deadline = timezone.now()
        period.save(update_fields=["availability_deadline"])
        log(actor, "availability.close", period)
