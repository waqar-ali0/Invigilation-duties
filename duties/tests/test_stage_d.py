from datetime import date

import pytest
from django.urls import reverse

from accounts.models import Role, User
from allocation.services import publish_period, run_allocation
from duties.attendance import mark_attendance, record_stand_in
from duties.handover import replace_duty
from duties.missed import MissedDutyError, assign_makeup, decide_absence
from duties.models import Duty, DutySource, DutyStatus, Obligation, ObligationStatus, RosterVersion
from duties.moves import MoveBlocked, move_duty
from duties.tests.conftest import move_slot_to_past
from duties.versions import compare, roster_snapshot
from exams.models import ExamPeriod, ExamSession, PeriodStatus, RoomAllocation, TimeSlot
from notifications.models import EmailKind, Notification, OutboundEmail
from notifications.outbox import queue_email
from workload.calc import period_loads
from workload.models import WorkloadPolicy

pytestmark = pytest.mark.django_db


def future_room(dept, name="Future Finals"):
    """A room in another, published period far in the future."""
    later = ExamPeriod.objects.create(name=name, start_date=date(2030, 6, 1), end_date=date(2030, 6, 10), status=PeriodStatus.PUBLISHED)
    slot = TimeSlot.objects.create(period=later, date=date(2030, 6, 2), start_time="09:00", end_time="12:00")
    session = ExamSession.objects.create(slot=slot, course_code="FUT1", course_name="Later", department=dept)
    return RoomAllocation.objects.create(session=session, room="Z9", student_count=30, invigilators_required=2)


def absent(published_duty, admin_user):
    move_slot_to_past(published_duty)
    return mark_attendance(published_duty, admin_user, present=False)


# --- missed duties, make-ups and their separate trace ---------------------------------------


def test_absence_decision_and_makeup_lifecycle(published_duty, admin_user, dept, make_faculty):
    duty = absent(published_duty, admin_user)
    with pytest.raises(MissedDutyError, match="reason"):
        decide_absence(duty, admin_user, "", excused=False, owe_makeup=True)
    obligation = decide_absence(duty, admin_user, "Did not come", excused=False, owe_makeup=True)
    assert obligation.status == ObligationStatus.OPEN and obligation.owed_by == duty.faculty
    room = future_room(dept)
    makeup = assign_makeup(obligation, room, admin_user)
    obligation.refresh_from_db()
    assert makeup.source == DutySource.MAKEUP and makeup.replaces == duty
    assert obligation.status == ObligationStatus.ASSIGNED
    assert OutboundEmail.objects.filter(kind=EmailKind.MAKEUP_ASSIGNED).exists()
    Duty.objects.filter(pk=makeup.pk).update(status=DutyStatus.COMPLETED)
    obligation.refresh_from_db()
    assert obligation.status == ObligationStatus.COMPLETED
    duty.refresh_from_db()
    assert duty.status == DutyStatus.ABSENT and duty.absence_excused is False  # the miss stays on record


def test_absentee_and_standin_are_traced_separately(published_duty, admin_user, make_faculty):
    duty = absent(published_duty, admin_user)
    cover = make_faculty("Cover")
    stand_in = record_stand_in(duty, cover, admin_user, "Covered on the day")
    decide_absence(duty, admin_user, "Ill", excused=True, owe_makeup=False)
    assert stand_in.faculty == cover and stand_in.replaces == duty and stand_in.status == DutyStatus.COMPLETED
    assert not Obligation.objects.exists()
    loads = {x.faculty.name: x for x in period_loads(duty.period)}
    assert loads["Owner Person"].missed == 1 and loads["Owner Person"].missed_excused == 1
    assert loads["Cover"].stand_ins == 1


def test_excused_absence_counts_only_when_the_policy_says_so(published_duty, admin_user):
    duty = absent(published_duty, admin_user)
    decide_absence(duty, admin_user, "Ill", excused=True, owe_makeup=False)
    owner = lambda: next(x for x in period_loads(duty.period) if x.faculty == duty.faculty)  # noqa: E731
    assert owner().counted == 1  # default policy: excused absences count
    policy = WorkloadPolicy.for_period(duty.period)
    policy.excused_absence_counts = False
    policy.save()
    assert owner().counted == 0


def test_missed_page_records_a_decision(admin_client, published_duty, admin_user):
    duty = absent(published_duty, admin_user)
    admin_client.post(reverse("missed_duties"), {"duty": duty.pk, "reason": "Train late", "excused": "yes", "makeup": "on"})
    duty.refresh_from_db()
    assert duty.absence_excused is True and Obligation.objects.get(original=duty)
    page = admin_client.get(reverse("missed_duties")).content.decode()
    assert "Make-up duties owed (1)" in page and "Train late" in page


# --- roster versions ------------------------------------------------------------------------


def test_publish_and_later_changes_keep_versions(small_period, admin_user, make_faculty):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    v1 = RosterVersion.objects.get(period=small_period)
    assert v1.number == 1 and v1.reason == "Published" and len(v1.snapshot) == Duty.objects.active().filter(period=small_period).count()
    duty = Duty.objects.filter(period=small_period).first()
    newcomer = make_faculty("Newcomer")
    replace_duty(duty, newcomer, admin_user, "Emergency")
    v2 = RosterVersion.objects.get(period=small_period, number=2)
    diff = compare(v1.snapshot, v2.snapshot)
    assert [r["faculty"] for r in diff["added"]] == ["Newcomer"]
    assert [r["faculty"] for r in diff["removed"]] == [duty.faculty.name]
    assert diff["rooms"] == [duty.room_allocation.room]
    assert compare(v2.snapshot, roster_snapshot(small_period))["added"] == []


def test_versions_page(admin_client, small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    page = admin_client.get(reverse("roster_versions", args=[small_period.pk])).content.decode()
    assert "v1" in page and "No differences" in page


# --- moving duties ------------------------------------------------------------------------------


def test_move_in_draft_changes_the_duty_in_place(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    slot1 = small_period.slots.order_by("date").first()
    duty = Duty.objects.filter(period=small_period, room_allocation__session__slot=slot1).first()
    other = RoomAllocation.objects.filter(session__slot=slot1).exclude(pk=duty.room_allocation_id).first()
    moved = move_duty(duty, other, admin_user)
    assert moved.pk == duty.pk and moved.room_allocation == other


def test_move_after_publishing_keeps_the_original_and_emails(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    slot1 = small_period.slots.order_by("date").first()
    duty = Duty.objects.filter(period=small_period, room_allocation__session__slot=slot1).first()
    other = RoomAllocation.objects.filter(session__slot=slot1).exclude(pk=duty.room_allocation_id).first()
    with pytest.raises(MoveBlocked, match="reason"):
        move_duty(duty, other, admin_user)
    new = move_duty(duty, other, admin_user, "Room swap")
    duty.refresh_from_db()
    assert duty.status == DutyStatus.CANCELLED and new.replaces == duty and new.source == DutySource.MOVED
    assert OutboundEmail.objects.filter(kind=EmailKind.DUTY_MOVED).count() == 1
    assert RosterVersion.objects.filter(period=small_period).count() == 2


# --- receipt, notifications, slips ----------------------------------------------------------------


def test_faculty_confirm_receipt(client, published_duty):
    client.force_login(published_duty.faculty.user)
    page = client.get(reverse("my_duties")).content.decode()
    assert "I have seen these duties (1 new)" in page
    client.post(reverse("acknowledge_duties", args=[published_duty.period_id]))
    published_duty.refresh_from_db()
    assert published_duty.acknowledged_at is not None
    assert "I have seen these duties" not in client.get(reverse("my_duties")).content.decode()


def test_emails_also_become_notifications_but_never_account_links(client, make_member):
    member = make_member("Noted")
    queue_email(member.user.email, "Your duties", "Hello\nSee http://testserver/my/duties/", EmailKind.REMINDER)
    queue_email(member.user.email, "Set up your account", "Link http://testserver/activate/x/", EmailKind.ACTIVATION)
    (n,) = Notification.objects.filter(user=member.user)
    assert n.title == "Your duties" and n.link == "/my/duties/" and "http" not in n.body
    client.force_login(member.user)
    page = client.get(reverse("my_duties")).content.decode()
    assert 'class="bell-count">1<' in page
    client.post(reverse("notifications"))
    n.refresh_from_db()
    assert n.read_at is not None


def test_no_address_means_no_email(make_faculty):
    assert queue_email(None, "x", "y", EmailKind.REMINDER) is None
    assert not OutboundEmail.objects.exists()


def test_admin_downloads_a_duty_slip(admin_client, published_duty):
    r = admin_client.get(reverse("faculty_duty_slip", args=[published_duty.faculty_id, published_duty.period_id]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_operations_pages_are_admin_only(client, published_duty):
    client.force_login(published_duty.faculty.user)
    for url in (reverse("missed_duties"), reverse("roster_versions", args=[published_duty.period_id]),
                reverse("faculty_duty_slip", args=[published_duty.faculty_id, published_duty.period_id])):
        assert client.get(url).status_code in (302, 403), url
