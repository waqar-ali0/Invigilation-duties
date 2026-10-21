"""Functions that queue the application's emails for particular events."""

from itertools import groupby

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from duties.models import Duty
from exams.models import PeriodStatus

from .models import EmailKind
from .outbox import queue_email
from .render import absolute_url, render_email


def _address(faculty):
    return faculty.user.email if faculty.user_id else faculty.email


def duty_ics_attachment(duty):
    """(filename, bytes, mimetype) calendar file for one duty."""
    from reports.personal import duties_ics

    ics = duties_ics(duty.faculty, [duty]).encode("utf-8")
    return (f"duty-{duty.pk}.ics", ics, "text/calendar")


def queue_swap_email(swap, name, recipients, attachment=None, **extra):
    """Queue the `emails/<name>_*` email about `swap` to each address in `recipients`."""
    subject, body = render_email(name, {"swap": swap, "link": absolute_url(reverse("swap_requests")), **extra})
    for to in recipients:
        queue_email(to, subject, body, name, attachment=attachment)


def address_of(faculty):
    return _address(faculty)


def queue_roster_published(period):
    """One email per faculty member with duties in `period`: their list plus a calendar file."""
    from reports.personal import duties_ics

    duties = (
        Duty.objects.active()
        .filter(period=period)
        .select_related("faculty__user", "room_allocation__session__slot")
        .order_by("faculty_id", "room_allocation__session__slot__date", "room_allocation__session__slot__start_time")
    )
    link = absolute_url(reverse("my_duties"))
    count = 0
    for faculty, group in groupby(duties, key=lambda d: d.faculty):
        group = list(group)
        subject, body = render_email(
            "roster_published", {"faculty": faculty, "period": period, "duties": group, "link": link}
        )
        ics = duties_ics(faculty, group).encode("utf-8")
        queue_email(
            _address(faculty),
            subject,
            body,
            EmailKind.ROSTER_PUBLISHED,
            attachment=(f"{slugify(period.name)}-duties.ics", ics, "text/calendar"),
        )
        count += 1
    return count


def queue_reminders(day):
    """Queue one reminder per faculty member with duties on `day` in a published period.

    Duties already reminded are skipped, so running this twice sends nothing new.
    """
    with transaction.atomic():
        duties = list(
            Duty.objects.active()
            .select_for_update()
            .filter(
                period__status=PeriodStatus.PUBLISHED,
                room_allocation__session__slot__date=day,
                reminder_sent_at__isnull=True,
            )
            .select_related("faculty__user", "room_allocation__session__slot", "period")
            .order_by("faculty_id", "room_allocation__session__slot__start_time")
        )
        link = absolute_url(reverse("my_duties"))
        count = 0
        for faculty, group in groupby(duties, key=lambda d: d.faculty):
            group = list(group)
            subject, body = render_email("reminder", {"faculty": faculty, "day": day, "duties": group, "link": link})
            queue_email(_address(faculty), subject, body, EmailKind.REMINDER)
            count += 1
        Duty.objects.filter(pk__in=[d.pk for d in duties]).update(reminder_sent_at=timezone.now())
    return count
