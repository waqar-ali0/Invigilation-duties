"""Regression tests for the minor findings of the Phase 3 review."""

import pytest
from django.db import connection
from django.urls import reverse

from accounts.models import Role, User
from audit.log import log
from audit.models import AuditLog
from duties.attendance import mark_attendance, mark_room_present
from duties.models import Duty, DutySource, DutyStatus, SwapStatus
from duties.swaps import MSG_NOT_SWAPPABLE, SwapError, accept_swap, expire_if_started, request_swap
from duties.tests.conftest import move_slot_to_past
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus
from reports.excel import operations_xlsx

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    return User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF)


@pytest.fixture
def started(published_duty):
    move_slot_to_past(published_duty)
    return published_duty


# 1. request_swap locks the period; accept refuses a duty that is no longer assigned


def test_request_swap_locks_the_period_inside_a_transaction(published_duty, make_member, monkeypatch):
    calls = []
    original = ExamPeriod.lock_published.__func__

    def spy(cls, pk):
        calls.append(connection.in_atomic_block)
        return original(cls, pk)

    monkeypatch.setattr(ExamPeriod, "lock_published", classmethod(spy))
    request_swap(published_duty, make_member())
    assert calls == [True]


def test_request_swap_on_unpublished_period_is_a_swap_error(published_duty, make_member):
    ExamPeriod.objects.filter(pk=published_duty.period_id).update(status=PeriodStatus.CLOSED)
    with pytest.raises(SwapError, match="Only a published roster can be changed this way."):
        request_swap(published_duty, make_member())


def test_accept_refuses_duty_no_longer_assigned(published_duty, make_member):
    peer = make_member()
    swap = request_swap(published_duty, peer)
    Duty.objects.filter(pk=published_duty.pk).update(status=DutyStatus.ABSENT)
    with pytest.raises(SwapError, match=MSG_NOT_SWAPPABLE):
        accept_swap(swap, peer)
    swap.refresh_from_db()
    assert swap.status == SwapStatus.PENDING_PEER


# 2. mark_room_present refuses a period that is not published, even with nothing to mark


def test_mark_room_present_refuses_closed_period_with_nothing_to_mark(started, staff):
    mark_attendance(started, staff, present=True)
    ExamPeriod.objects.filter(pk=started.period_id).update(status=PeriodStatus.CLOSED)
    with pytest.raises(PeriodLockedError):
        mark_room_present(started.room_allocation, staff)


def test_mark_room_present_is_all_or_nothing(started, staff, make_member, monkeypatch):
    other = Duty.objects.create(faculty=make_member(), room_allocation=started.room_allocation, source=DutySource.AUTO)
    import duties.attendance as attendance

    original = attendance.mark_attendance

    def failing(duty, actor, present):
        if duty.pk == max(started.pk, other.pk):
            raise attendance.AttendanceError("boom")
        return original(duty, actor, present)

    monkeypatch.setattr(attendance, "mark_attendance", failing)
    with pytest.raises(attendance.AttendanceError):
        mark_room_present(started.room_allocation, staff)
    assert set(Duty.objects.filter(pk__in=[started.pk, other.pk]).values_list("status", flat=True)) == {DutyStatus.ASSIGNED}


# 3. attendance_mark accepts only "1" or "0"


def test_attendance_mark_rejects_unknown_value(client, started, staff):
    client.force_login(staff)
    r = client.post(reverse("attendance_mark", args=[started.pk]), {"present": "yes"}, follow=True)
    assert "Choose Present or Absent." in r.content.decode()
    started.refresh_from_db()
    assert started.status == DutyStatus.ASSIGNED
    r = client.post(reverse("attendance_mark", args=[started.pk]), {}, follow=True)
    started.refresh_from_db()
    assert started.status == DutyStatus.ASSIGNED


# 4. My Duties does not query the period settings once per duty


def test_my_duties_query_count_does_not_grow_with_duties(client, published_duty, make_room, django_assert_max_num_queries):
    for day in range(3, 9):
        Duty.objects.create(faculty=published_duty.faculty, room_allocation=make_room(day=day, room=f"R{day}"), source=DutySource.AUTO)
    client.force_login(published_duty.faculty.user)
    with django_assert_max_num_queries(8):
        assert client.get(reverse("my_duties")).status_code == 200


# 5. candidate lists explain their order


def test_candidate_pages_explain_order(client, admin_client, published_duty, make_member):
    make_member("Free Colleague")
    text = "best matches first (no rule warnings, then fewest duties)"
    assert text in admin_client.get(reverse("replace_duty", args=[published_duty.period_id, published_duty.pk])).content.decode()
    client.force_login(published_duty.faculty.user)
    assert text in client.get(reverse("ask_colleague", args=[published_duty.pk])).content.decode()


# 6. the operations report builds the faculty records once


def test_operations_report_builds_records_once(admin_client, published_duty, monkeypatch):
    import reports.operations as operations
    import reports.views as views

    calls = []
    original = operations.faculty_records

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(operations, "faculty_records", spy)
    monkeypatch.setattr(views, "faculty_records", spy)
    assert admin_client.get(reverse("operations_report", args=[published_duty.period_id])).status_code == 200
    assert len(calls) == 1
    calls.clear()
    operations_xlsx(published_duty.period)
    assert len(calls) == 1


# 7. the audit period filter uses subqueries and covers slots, sessions and rooms


def test_audit_period_filter_covers_exam_structure_without_queries(admin_user, published_duty, django_assert_num_queries):
    from audit.views import _period_filter

    room = published_duty.room_allocation
    for obj in (room, room.session, room.session.slot, published_duty):
        log(admin_user, "test.touch", obj)
    period = published_duty.period
    with django_assert_num_queries(0):
        q = _period_filter(period)
    found = set(AuditLog.objects.filter(q).values_list("object_type", flat=True))
    assert found == {"exams.RoomAllocation", "exams.ExamSession", "exams.TimeSlot", "duties.Duty"}

    other = ExamPeriod.objects.create(name="Other", start_date=period.start_date, end_date=period.end_date)
    assert not AuditLog.objects.filter(_period_filter(other)).exists()


# 8. the attendance page only offers buttons the user may use


def test_attendance_slot_hides_buttons_user_cannot_use(client, started, staff):
    mark_attendance(started, staff, present=True)
    other_staff = User.objects.create_user("other-staff@uni.edu", "x", role=Role.EXAM_STAFF)
    url = reverse("attendance_slot", args=[started.room_allocation.session.slot_id])
    mark_url = reverse("attendance_mark", args=[started.pk])
    client.force_login(other_staff)
    assert mark_url not in client.get(url).content.decode()
    client.force_login(staff)
    assert mark_url in client.get(url).content.decode()


# 9. expiring a swap is audited


def test_expiry_is_audited_once(published_duty, make_member):
    swap = request_swap(published_duty, make_member())
    move_slot_to_past(published_duty)
    swap.duty.refresh_from_db()
    expire_if_started(swap)
    expire_if_started(swap)
    entries = AuditLog.objects.filter(action="swap.expire")
    assert swap.status == SwapStatus.EXPIRED
    assert [(e.actor, e.object_id) for e in entries] == [(None, str(swap.pk))]
