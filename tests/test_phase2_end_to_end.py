import re
from datetime import date, timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from duties.models import Duty, Unavailability, UnavailabilityStatus
from notifications.models import EmailKind, EmailStatus, OutboundEmail
from notifications.outbox import send_queued

pytestmark = pytest.mark.django_db
PASSWORD = "Strong-pass-123"


def activate(person, mailoutbox):
    """Open the activation link from the email actually sent to `person` and set a password."""
    message = next(m for m in mailoutbox if m.to == [person.email.lower()] and "Set up" in m.subject)
    link = re.search(r"https?://\S+/activate/\S+", message.body).group(0).replace(settings.SITE_URL, "")
    client = Client()
    r = client.post(link, {"new_password1": PASSWORD, "new_password2": PASSWORD})
    assert r.status_code == 302
    return client


def test_invite_request_approve_allocate_publish_remind(admin_client, period, make_room, make_faculty, dept, mailoutbox, capsys):
    hod = make_faculty("Head Of Dept")
    alice = make_faculty("Alice")
    make_faculty("Bob")
    make_faculty("Carol")
    dept.hod = hod
    dept.save()
    day1 = make_room(day=1, course="CS101", room="R1", needed=2)
    make_room(day=2, course="CS102", room="R2", needed=2)

    # Exam office invites everyone; emails go out through the outbox.
    admin_client.post(reverse("invite_all"))
    assert send_queued() == (4, 0)
    alice_client = activate(alice, mailoutbox)
    hod_client = activate(hod, mailoutbox)

    # Availability window opens; Alice asks for 1 December off.
    deadline = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    admin_client.post(reverse("open_availability", args=[period.pk]), {"deadline": deadline})
    assert OutboundEmail.objects.filter(kind=EmailKind.AVAILABILITY_OPEN).count() == 4  # everyone with an account
    alice_client.post(
        reverse("my_unavailability"), {"period": period.pk, "date": "2026-12-01", "slot": "", "reason": "Conference"}
    )
    req = Unavailability.objects.get(faculty=alice)

    # The HOD approves it.
    hod_client.post(reverse("decide_request", args=[req.pk]), {"decision": "approve", "comment": "OK"})
    req.refresh_from_db()
    assert req.status == UnavailabilityStatus.APPROVED

    # Allocation respects it; the roster is published.
    admin_client.post(reverse("run_allocation", args=[period.pk]))
    assert not Duty.objects.filter(faculty=alice, room_allocation=day1).exists()
    assert Duty.objects.filter(room_allocation=day1).count() == 2
    admin_client.post(reverse("publish", args=[period.pk]), {"acknowledge_unfilled": "on", "confirm_rules": "on"})
    assert OutboundEmail.objects.filter(kind=EmailKind.ROSTER_PUBLISHED).count() == Duty.objects.values("faculty").distinct().count()

    # Alice sees her own duties and downloads them.
    page = alice_client.get(reverse("my_duties")).content.decode()
    alice_duties = Duty.objects.filter(faculty=alice)
    for d in alice_duties:
        assert d.room_allocation.room in page
    ics = alice_client.get(reverse("my_duties_ics", args=[period.pk]))
    assert ics.content.count(b"BEGIN:VEVENT") == alice_duties.count()

    # Reminders for the first exam day, then everything is sent.
    call_command("send_reminders", "--date", date(2026, 12, 1).isoformat())
    assert "Queued 2 reminder(s)" in capsys.readouterr().out
    call_command("send_emails")
    assert "failed 0" in capsys.readouterr().out
    assert not OutboundEmail.objects.exclude(status=EmailStatus.SENT).exists()
