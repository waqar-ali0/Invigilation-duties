from datetime import date, datetime, timedelta, time

import pytest
from django.urls import reverse
from django.utils import timezone

from allocation.engine import EngineInput, Person, Preference, Room, Rules, Slot, allocate
from allocation.services import publish_period, run_allocation
from audit.models import AuditLog
from duties.backups import reserve_backups
from duties.handover import handover_candidates, replace_duty
from duties.models import (
    BackupMember,
    BackupReservation,
    BackupRole,
    Duty,
    DutySource,
    Preference as PreferenceRow,
    PreferenceKind,
    ReservationStatus,
)
from duties.services import AssignmentNeedsConfirmation, add_duty

pytestmark = pytest.mark.django_db
RULES = Rules(max_per_day=2, avoid_back_to_back=False, exclude_course_teacher=False)


# --- engine -----------------------------------------------------------------------------


def test_engine_follows_preferences_among_equals_and_reports_them():
    d1, d2 = date(2026, 12, 1), date(2026, 12, 2)
    slots = [Slot(1, d1, 0), Slot(2, d2, 0)]
    rooms = [Room(10, 1, 1), Room(20, 2, 1)]
    people = [Person(1, 1, None), Person(2, 1, None)]
    prefs = {1: Preference(dates=frozenset({d2})), 2: Preference(dates=frozenset({d1}))}
    for seed in range(1, 6):
        res = allocate(EngineInput(slots, rooms, people, RULES, preferences=prefs), seed)
        assert sorted(res.assignments) == [(1, 20), (2, 10)]
        assert (res.preferences_met, res.preferences_total) == (2, 2)


def test_preferences_never_override_fairness_or_block():
    d = date(2026, 12, 1)
    slots = [Slot(i, d + timedelta(days=i), 0) for i in range(4)]
    rooms = [Room(100 + i, i, 1) for i in range(4)]
    people = [Person(1, 1, None), Person(2, 1, None)]
    prefs = {1: Preference(dates=frozenset(s.date for s in slots))}  # person 1 would like every day
    res = allocate(EngineInput(slots, rooms, people, RULES, preferences=prefs), seed=3)
    counts = [sum(1 for p, _ in res.assignments if p == pid) for pid in (1, 2)]
    assert counts == [2, 2]  # still shared equally
    only = allocate(EngineInput(slots[:1], rooms[:1], [Person(1, 1, None)], RULES, preferences={1: Preference(avoid_slots=frozenset({0}))}), seed=1)
    assert only.assignments == [(1, 100)]  # a wish to avoid never leaves a seat empty


def test_reserve_only_members_fill_only_shortages_when_allowed():
    slots = [Slot(1, date(2026, 12, 1), 0)]
    rooms = [Room(10, 1, 2)]
    people = [Person(1, 1, None), Person(9, 1, None, reserve=True)]
    held_back = allocate(EngineInput(slots, rooms, people, RULES), seed=1)
    assert held_back.assignments == [(1, 10)] and "kept in reserve" in held_back.unfilled[0].reason
    allowed = Rules(2, False, False, reserves_fill_shortages=True)
    used = allocate(EngineInput(slots, rooms, people, allowed), seed=1)
    assert sorted(used.assignments) == [(1, 10), (9, 10)]


# --- locking and partial re-runs ---------------------------------------------------------


def test_locked_duties_survive_reruns(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    keep = Duty.objects.filter(period=small_period).first()
    Duty.objects.filter(pk=keep.pk).update(locked=True)
    run_allocation(small_period, admin_user, seed=99)
    assert Duty.objects.filter(pk=keep.pk, locked=True).exists()


def test_rerun_one_slot_leaves_the_others_untouched(small_period, admin_user, admin_client):
    run_allocation(small_period, admin_user, seed=1)
    slot1, slot2 = list(small_period.slots.order_by("date"))
    other = set(Duty.objects.filter(period=small_period, room_allocation__session__slot=slot2).values_list("pk", flat=True))
    admin_client.post(reverse("run_allocation", args=[small_period.pk]), {"slot": [str(slot1.pk)]})
    assert set(Duty.objects.filter(period=small_period, room_allocation__session__slot=slot2).values_list("pk", flat=True)) == other
    assert Duty.objects.filter(period=small_period, room_allocation__session__slot=slot1).count() == 4


def test_lock_button(admin_client, small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    d = Duty.objects.filter(period=small_period).first()
    admin_client.post(reverse("lock_duty", args=[small_period.pk, d.pk]), {"locked": "1"})
    d.refresh_from_db()
    assert d.locked and AuditLog.objects.filter(action="duty.lock").exists()


# --- preferences through the site --------------------------------------------------------


def test_faculty_add_preferences_only_while_availability_is_open(client, make_member, period):
    member = make_member("Pref Person")
    client.force_login(member.user)
    url = reverse("my_preferences")
    assert "Nothing open right now" in client.get(url).content.decode()
    period.status = "availability_open"
    period.availability_deadline = timezone.now() + timedelta(days=3)
    period.save()
    client.post(url, {"period": period.pk, f"p{period.pk}-kind": PreferenceKind.DATE, f"p{period.pk}-date": "2026-12-02"})
    pref = PreferenceRow.objects.get(faculty=member)
    assert pref.date == date(2026, 12, 2)
    client.post(url, {"period": period.pk, f"p{period.pk}-kind": PreferenceKind.DATE, f"p{period.pk}-date": "2027-01-09"})
    assert PreferenceRow.objects.count() == 1  # outside the period: refused


def test_manual_assignment_against_a_preference_needs_a_reason(room, make_faculty, admin_user, period):
    f = make_faculty("Wishful")
    PreferenceRow.objects.create(faculty=f, period=period, kind=PreferenceKind.AVOID_SLOT, slot=room.session.slot)
    with pytest.raises(AssignmentNeedsConfirmation) as e:
        add_duty(f, room, admin_user)
    assert "Goes against their preferences" in e.value.warnings
    add_duty(f, room, admin_user, allow_warnings=True, reason="Only person free")
    entry = AuditLog.objects.get(action="duty.assign")
    assert entry.after["override_reason"] == "Only person free"


# --- backups ---------------------------------------------------------------------------------


def test_backups_reserved_per_slot_and_released_when_busy(small_period, admin_user, make_faculty):
    lab = [make_faculty(f"Lab {i}", designation="lab_engineer") for i in range(2)]
    for i, f in enumerate(lab):
        BackupMember.objects.create(faculty=f, role=BackupRole.RESERVE_ONLY, priority=i + 1)
    small_period.settings.backups_per_slot = 1
    small_period.settings.save()
    run_allocation(small_period, admin_user, seed=1)
    reservations = BackupReservation.objects.filter(period=small_period)
    assert reservations.count() == small_period.slots.count()
    assert not Duty.objects.filter(faculty__in=lab).exists()  # backup-only: no regular duties
    slot = small_period.slots.first()
    reserved = reservations.get(slot=slot)
    room = slot.sessions.first().rooms.first()
    Duty.objects.create(faculty=reserved.faculty, room_allocation=room, source=DutySource.MANUAL)
    reserve_backups(small_period)
    assert not BackupReservation.objects.filter(pk=reserved.pk).exists()  # busy now: released
    assert BackupReservation.objects.filter(slot=slot).exclude(faculty=reserved.faculty).exists()


def test_replacement_suggests_reserved_backup_first_and_records_the_call(small_period, admin_user, make_faculty):
    backup = make_faculty("Standby", designation="lab_engineer")
    BackupMember.objects.create(faculty=backup, role=BackupRole.ALSO_BACKUP, priority=1)
    small_period.settings.backups_per_slot = 1
    small_period.settings.save()
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    reservation = BackupReservation.objects.filter(period=small_period, faculty=backup).first()
    assert reservation is not None
    duty = Duty.objects.filter(period=small_period, room_allocation__session__slot=reservation.slot).exclude(faculty=backup).first()
    (first, _, _), *_ = handover_candidates(duty, require_account=False)
    assert first == backup and first.backup_label == "Reserved backup"
    new = replace_duty(duty, backup, admin_user, "Absent, called backup")
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.CALLED and reservation.duty == new
    duty.refresh_from_db()
    assert duty.status == "cancelled"  # the original duty stays on record


def test_backup_pages(admin_client, small_period, make_faculty):
    f = make_faculty("Lab", designation="lab_engineer")
    admin_client.post(reverse("backup_pool"), {"action": "add_category", "category": f.category_id, "role": BackupRole.RESERVE_ONLY})
    assert BackupMember.objects.get(faculty=f).role == BackupRole.RESERVE_ONLY
    admin_client.post(reverse("period_backups", args=[small_period.pk]), {"action": "settings", "backups_per_slot": "1"})
    assert BackupReservation.objects.filter(period=small_period).count() == small_period.slots.count()
    assert "Reserved as backup" in admin_client.get(reverse("period_backups", args=[small_period.pk])).content.decode()


# --- checks -----------------------------------------------------------------------------------


def test_check_page_explains_shortages_and_fixes(admin_client, short_period, admin_user):
    run_allocation(short_period, admin_user, seed=1)
    page = admin_client.get(reverse("period_check", args=[short_period.pk])).content.decode()
    assert "Not enough capacity" in page or "seat(s) unfilled" in page
    assert "What to do" in page and "Workload rules not confirmed" in page
