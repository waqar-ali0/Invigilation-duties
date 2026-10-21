from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from duties.models import Unavailability, UnavailabilityStatus
from duties.unavailability import RequestRejected, submit_request, withdraw_request
from exams.availability import open_availability
from exams.models import ExamPeriod, TimeSlot

pytestmark = pytest.mark.django_db
D = date(2026, 12, 2)


@pytest.fixture
def open_period(period, make_room, admin_user):
    make_room(day=2, start="09:00", end="12:00", course="A")
    make_room(day=2, start="14:00", end="17:00", course="B")
    open_availability(period, timezone.now() + timedelta(days=3), admin_user)
    period.refresh_from_db()
    return period


def slots(period):
    return list(TimeSlot.objects.filter(period=period).order_by("start_time"))


def test_submit_whole_day(open_period, make_member):
    me = make_member()
    req = submit_request(me, open_period, D, None, "  Conference  ")
    assert (req.status, req.slot, req.reason) == (UnavailabilityStatus.PENDING, None, "Conference")


def errors_of(fn, *args):
    with pytest.raises(RequestRejected) as e:
        fn(*args)
    return e.value.errors


def test_exempt_rejected(open_period, make_member):
    assert errors_of(submit_request, make_member(is_exempt=True), open_period, D, None, "x") == [
        "You are exempt from invigilation."
    ]


def test_closed_window_rejected(period, make_member):
    assert errors_of(submit_request, make_member(), period, D, None, "x") == [
        "The availability window for this period is closed."
    ]


def test_date_outside_period_and_blank_reason(open_period, make_member):
    assert errors_of(submit_request, make_member(), open_period, date(2027, 1, 1), None, "  ") == [
        "Date is outside the exam period.",
        "A reason is required.",
    ]


def test_slot_on_other_date_rejected(open_period, make_member):
    morning = slots(open_period)[0]
    assert errors_of(submit_request, make_member(), open_period, date(2026, 12, 3), morning, "x") == [
        "That time slot is not on the chosen date."
    ]


def test_slot_of_other_period_rejected(open_period, make_member):
    other = ExamPeriod.objects.create(name="Other", start_date=D, end_date=D)
    foreign = TimeSlot.objects.create(period=other, date=D, start_time=slots(open_period)[0].start_time, end_time=slots(open_period)[0].end_time)
    assert errors_of(submit_request, make_member(), open_period, D, foreign, "x") == [
        "That time slot is not on the chosen date."
    ]


@pytest.mark.parametrize("first,second", [("day", "slot"), ("slot", "day"), ("slot", "slot")])
def test_duplicates_rejected(open_period, make_member, first, second):
    me = make_member()
    morning = slots(open_period)[0]
    submit_request(me, open_period, D, None if first == "day" else morning, "x")
    assert errors_of(submit_request, me, open_period, D, None if second == "day" else morning, "y") == [
        "You already have a request covering this date and slot."
    ]


def test_different_slots_same_day_allowed(open_period, make_member):
    me = make_member()
    morning, afternoon = slots(open_period)
    submit_request(me, open_period, D, morning, "x")
    submit_request(me, open_period, D, afternoon, "y")
    assert Unavailability.objects.filter(faculty=me).count() == 2


def test_rejected_or_withdrawn_request_does_not_block_new_one(open_period, make_member):
    me = make_member()
    req = submit_request(me, open_period, D, None, "x")
    withdraw_request(req, me)
    submit_request(me, open_period, D, None, "again")


def test_withdraw_rules(open_period, make_member):
    me, other = make_member(), make_member()
    req = submit_request(me, open_period, D, None, "x")
    with pytest.raises(PermissionDenied):
        withdraw_request(req, other)
    withdraw_request(req, me)
    req.refresh_from_db()
    assert req.status == UnavailabilityStatus.WITHDRAWN
    assert errors_of(withdraw_request, req, me) == ["Only pending requests can be withdrawn."]


def test_submit_locks_faculty_row(open_period, make_member):
    from unittest import mock

    from django.db.models import QuerySet

    from faculty.models import Faculty

    me = make_member()
    with mock.patch.object(QuerySet, "select_for_update", autospec=True, side_effect=QuerySet.select_for_update) as spy:
        submit_request(me, open_period, D, None, "x")
    assert any(call.args[0].model is Faculty for call in spy.call_args_list)


def test_withdraw_is_audited(open_period, make_member):
    from audit.models import AuditLog

    me = make_member()
    req = submit_request(me, open_period, D, None, "x")
    withdraw_request(req, me)
    entry = AuditLog.objects.get(action="unavailability.withdraw")
    assert entry.actor == me.user and entry.object_id == str(req.pk)
