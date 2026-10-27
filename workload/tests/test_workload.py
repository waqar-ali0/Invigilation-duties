from collections import Counter
from datetime import date

import pytest
from django.urls import reverse

from allocation.services import PublishBlocked, publish_period, run_allocation
from duties.models import Duty, DutySource, DutyStatus
from duties.services import AssignmentBlocked, add_duty
from exams.models import ExamPeriod
from faculty.models import Designation, FacultyCategory
from workload.calc import Flag, period_loads
from workload.models import CategoryRule, FacultyRule, TargetKind, WorkloadPolicy, WorkloadVersion
from workload.rules import resolve_rules
from workload.services import copy_rules

pytestmark = pytest.mark.django_db


def busy_period(make_room, rooms=6, needed=2):
    """`rooms` rooms on different days, each needing `needed` invigilators."""
    return [make_room(day=d + 1, course=f"C{d}", room=f"R{d}", needed=needed) for d in range(rooms)]


def totals(period):
    return Counter(Duty.objects.active().filter(period=period).values_list("faculty__name", flat=True))


def test_new_period_has_no_built_in_duty_numbers(period, make_faculty):
    f = make_faculty("A")
    rule = resolve_rules(period)[f.pk]
    assert period.settings.max_duties_per_faculty is None
    assert rule.target is None and rule.maximum is None and rule.describe() == "Target not set"
    (load,) = period_loads(period)
    assert Flag.NO_TARGET in load.flags and load.remaining is None  # unset is never shown as 0


def test_office_changes_targets_on_the_rules_page_and_engine_follows(admin_client, period, make_room, make_faculty, admin_user):
    busy_period(make_room, rooms=6, needed=2)  # 12 seats
    for name in ("L1", "L2", "L3", "L4"):
        make_faculty(name)
    lecturer = FacultyCategory.get(Designation.LECTURER)
    r = admin_client.post(reverse("workload_rules", args=[period.pk]), {
        "action": "categories", f"c{lecturer.pk}-target": "2", f"c{lecturer.pk}-kind": TargetKind.MAXIMUM,
    })
    assert r.status_code == 302
    run_allocation(period, admin_user, seed=1)
    assert max(totals(period).values()) == 2  # nobody above the maximum
    assert sum(totals(period).values()) == 8  # 4 seats stay empty rather than break the rule


def test_same_designation_different_personal_targets(period, make_room, make_faculty, admin_user):
    busy_period(make_room, rooms=6, needed=2)
    a, b, c = make_faculty("A"), make_faculty("B"), make_faculty("C")
    FacultyRule.objects.create(period=period, faculty=a, target=6, kind=TargetKind.EXACT)
    FacultyRule.objects.create(period=period, faculty=b, target=2, kind=TargetKind.EXACT)
    run_allocation(period, admin_user, seed=1)
    t = totals(period)
    assert (t["A"], t["B"], t["C"]) == (6, 2, 4)


def test_minimum_is_filled_before_sharing(period, make_room, make_faculty, admin_user):
    busy_period(make_room, rooms=4, needed=1)
    a = make_faculty("A")
    make_faculty("B"), make_faculty("C"), make_faculty("D")
    FacultyRule.objects.create(period=period, faculty=a, min_duties=3)
    run_allocation(period, admin_user, seed=1)
    assert totals(period)["A"] == 3


def test_visiting_faculty_duties_per_course(period, make_room, make_faculty, admin_user):
    rooms = busy_period(make_room, rooms=6, needed=2)
    visiting = make_faculty("V", designation=Designation.VISITING)
    for r in rooms[:2]:
        r.session.course_teachers.add(visiting)
    for name in ("L1", "L2", "L3"):
        make_faculty(name)
    CategoryRule.objects.create(period=period, category=visiting.category, per_course=1, kind=TargetKind.EXACT)
    rule = resolve_rules(period)[visiting.pk]
    assert (rule.target, rule.note) == (2, "1 per course × 2 courses")
    run_allocation(period, admin_user, seed=1)
    assert totals(period)["V"] == 2


def test_exempt_this_period_and_inactive_exception(period, make_room, make_faculty, admin_user, room):
    a, b = make_faculty("A"), make_faculty("B", is_active=False)
    FacultyRule.objects.create(period=period, faculty=a, exempt=True, reason="Sabbatical")
    with pytest.raises(AssignmentBlocked):
        add_duty(a, room, admin_user)
    with pytest.raises(AssignmentBlocked):
        add_duty(b, room, admin_user)
    FacultyRule.objects.create(period=period, faculty=b, allow_inactive=True, reason="Agreed with dean")
    assert add_duty(b, room, admin_user).faculty == b


def test_rules_are_per_period_and_copying_never_changes_the_source(period, make_faculty):
    later = ExamPeriod.objects.create(name="Spring", start_date=date(2027, 5, 1), end_date=date(2027, 5, 10))
    lecturer = FacultyCategory.get(Designation.LECTURER)
    f = make_faculty("A")
    CategoryRule.objects.create(period=period, category=lecturer, target=8, kind=TargetKind.MAXIMUM)
    FacultyRule.objects.create(period=period, faculty=f, target=3, kind=TargetKind.EXACT)
    copy_rules(period, later, None)
    CategoryRule.objects.filter(period=later).update(target=6)
    assert CategoryRule.objects.get(period=period).target == 8  # the earlier period is untouched
    assert resolve_rules(period)[f.pk].target == 3 and resolve_rules(later)[f.pk].target == 3
    assert CategoryRule.objects.get(period=later).target == 6


def test_assigned_is_not_completed_and_counting_policy_applies(published_period):
    duties = list(Duty.objects.filter(period=published_period).select_related("faculty"))
    d = duties[0]
    load = next(x for x in period_loads(published_period) if x.faculty == d.faculty)
    assert load.pending >= 1 and load.completed == 0  # assigned duties are pending, not completed
    Duty.objects.filter(pk=d.pk).update(status=DutyStatus.ABSENT)
    before = next(x for x in period_loads(published_period) if x.faculty == d.faculty).counted
    policy = WorkloadPolicy.for_period(published_period)
    policy.unexcused_absence_counts = True
    policy.save()
    after = next(x for x in period_loads(published_period) if x.faculty == d.faculty).counted
    assert after == before + 1


def test_duties_of_combined_periods_count_toward_the_cap(period, make_room, make_faculty, admin_user):
    mid = ExamPeriod.objects.create(name="Midterms", start_date=date(2026, 10, 1), end_date=date(2026, 10, 5))
    a, b = make_faculty("A"), make_faculty("B")
    from exams.models import ExamSession, RoomAllocation, TimeSlot

    slot = TimeSlot.objects.create(period=mid, date=date(2026, 10, 1), start_time="09:00", end_time="12:00")
    session = ExamSession.objects.create(slot=slot, course_code="M1", course_name="M", department=a.department)
    for i in range(3):
        Duty.objects.create(faculty=a, room_allocation=RoomAllocation.objects.create(session=session, room=f"M{i}", student_count=10, invigilators_required=1), source=DutySource.MANUAL)
    busy_period(make_room, rooms=6, needed=1)
    period.settings.max_duties_per_faculty = 4
    period.settings.save()
    WorkloadPolicy.for_period(period).combined_periods.add(mid)
    result = run_allocation(period, admin_user, seed=1)
    t = totals(period)
    assert t["A"] == 1 and t["B"] == 4  # A already had 3 of their 4 from the midterms
    assert sum(u.count for u in result.unfilled) == 1
    load = next(x for x in period_loads(period) if x.faculty == a)
    assert (load.prior, load.counted) == (3, 4)


def test_confirmation_versions_and_publishing(admin_client, small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    with pytest.raises(PublishBlocked, match="workload rules have not been confirmed"):
        publish_period(small_period, admin_user)
    admin_client.post(reverse("workload_rules", args=[small_period.pk]), {"action": "confirm"})
    assert WorkloadVersion.objects.get(period=small_period).number == 1
    publish_period(small_period, admin_user)
    lecturer = FacultyCategory.get(Designation.LECTURER)
    admin_client.post(reverse("workload_rules", args=[small_period.pk]), {"action": "categories", f"c{lecturer.pk}-max_duties": "5"})
    versions = list(WorkloadVersion.objects.filter(period=small_period).order_by("number"))
    assert [v.number for v in versions] == [1, 2]
    assert versions[1].reason.startswith("Changed after publishing") and versions[1].snapshot["categories"][0]["max"] == 5
    page = admin_client.get(reverse("workload_version", args=[small_period.pk, 1])).content.decode()
    assert "version 1" in page


def test_people_page_filters_and_flags(admin_client, small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    lecturer = FacultyCategory.get(Designation.LECTURER)
    CategoryRule.objects.create(period=small_period, category=lecturer, target=5, kind=TargetKind.EXACT)
    page = admin_client.get(reverse("workload_people", args=[small_period.pk]), {"flag": Flag.BELOW}).content.decode()
    assert "Below target" in page and "Exactly 5" in page
    rules_page = admin_client.get(reverse("workload_rules", args=[small_period.pk])).content.decode()
    assert "invigilator seats" in rules_page


def test_invalid_rule_is_refused(admin_client, period):
    lecturer = FacultyCategory.get(Designation.LECTURER)
    r = admin_client.post(reverse("workload_rules", args=[period.pk]), {
        "action": "categories", f"c{lecturer.pk}-min_duties": "5", f"c{lecturer.pk}-max_duties": "2",
    })
    assert r.status_code == 200 and "can&#x27;t be below the minimum" in r.content.decode()
    assert not CategoryRule.objects.exists()
