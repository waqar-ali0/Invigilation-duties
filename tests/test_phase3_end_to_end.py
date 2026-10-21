import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from allocation.services import publish_period
from duties.models import Duty, DutySource, DutyStatus, SwapRequest, SwapStatus
from exams.models import TimeSlot
from notifications.models import EmailStatus, OutboundEmail
from reports.operations import faculty_records, handover_history

pytestmark = pytest.mark.django_db


def as_user(user):
    c = Client()
    c.force_login(user)
    return c


def test_swap_replace_attend_report_audit(admin_client, admin_user, period, make_room, make_member, capsys):
    alice, bob, carol, dan = (make_member(n) for n in ("Alice", "Bob", "Carol", "Dan"))
    room = make_room(day=2, room="R1", needed=2)
    alice_duty = Duty.objects.create(faculty=alice, room_allocation=room, source=DutySource.AUTO)
    dan_duty = Duty.objects.create(faculty=dan, room_allocation=room, source=DutySource.AUTO)
    publish_period(period, admin_user)

    # Alice asks Bob, Bob accepts, the exam office approves.
    as_user(alice.user).post(reverse("ask_colleague", args=[alice_duty.pk]), {"colleague_id": bob.pk, "message": "Conference"})
    swap = SwapRequest.objects.get()
    as_user(bob.user).post(reverse("swap_respond", args=[swap.pk]), {"action": "accept"})
    admin_client.post(reverse("swap_decide", args=[swap.pk]), {"decision": "approve"})
    swap.refresh_from_db()
    assert swap.status == SwapStatus.APPROVED
    bob_duty = swap.new_duty

    # Bob falls ill; the exam office replaces him with Carol.
    admin_client.post(reverse("replace_duty", args=[period.pk, bob_duty.pk]), {"faculty_id": carol.pk, "reason": "Sick"})
    carol_duty = Duty.objects.get(faculty=carol, status=DutyStatus.ASSIGNED)
    assert carol_duty.replaces == bob_duty

    # Exam day: the slot is moved to today (already started) and exam staff mark attendance.
    TimeSlot.objects.filter(pk=room.session.slot_id).update(date=timezone.localdate(), start_time="00:00", end_time="23:59")
    staff = as_user(User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF))
    staff.post(reverse("attendance_mark", args=[carol_duty.pk]), {"present": "1"})
    staff.post(reverse("attendance_mark", args=[dan_duty.pk]), {"present": "0"})

    # Reports reflect the whole story.
    records = {r.faculty.name: r for r in faculty_records(period)}
    assert (records["Alice"].swapped_away, records["Alice"].assigned) == (1, 0)
    assert (records["Bob"].taken_over, records["Bob"].replaced, records["Bob"].assigned) == (1, 1, 0)
    assert (records["Carol"].completed, records["Carol"].taken_over) == (1, 1)
    assert (records["Dan"].absent, records["Dan"].completed) == (1, 0)
    assert [h["type"] for h in handover_history(period)] == ["Replacement", "Swap"]

    # The audit viewer lists each step.
    page = admin_client.get(reverse("audit_log"), {"period": period.pk}).content.decode()
    for action in ("swap.request", "swap.accept", "swap.approve", "duty.replace", "duty.attendance"):
        assert action in page

    # Every email goes out.
    call_command("send_emails")
    assert "failed 0" in capsys.readouterr().out
    assert not OutboundEmail.objects.exclude(status=EmailStatus.SENT).exists()
