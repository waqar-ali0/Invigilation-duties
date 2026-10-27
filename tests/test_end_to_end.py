import time as clock
from collections import Counter
from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from allocation.services import run_allocation
from audit.models import AuditLog
from duties.models import Duty
from exams.models import ExamPeriod, PeriodStatus
from exams.tests.helpers import FACULTY_HEADER, SCHEDULE_HEADER, make_xlsx
from faculty.models import Faculty
from imports.tests.helpers import run_import

pytestmark = pytest.mark.django_db


def assert_roster_invariants(period):
    duties = list(Duty.objects.active().filter(period=period).select_related("faculty", "room_allocation__session__slot"))
    per_slot = Counter((d.faculty_id, d.room_allocation.session.slot_id) for d in duties)
    assert max(per_slot.values(), default=0) == 1, "someone sits in two rooms at once"
    per_day = Counter((d.faculty_id, d.room_allocation.session.slot.date) for d in duties)
    assert max(per_day.values(), default=0) <= period.settings.max_duties_per_day
    totals = Counter(d.faculty_id for d in duties)
    for d in duties:
        f = d.faculty
        assert f.is_active and not f.is_exempt
        cap = period.cap_for(f.category_id)
        assert cap is None or totals[f.id] <= cap


def test_import_allocate_publish_export(admin_client):
    admin_client.post(reverse("department_import"), {"file": make_xlsx([["Name"], ["Physics"], ["Maths"]])})
    faculty_rows = [FACULTY_HEADER]
    for i in range(12):
        dept = "Physics" if i % 2 else "Maths"
        designation = "Professor" if i < 2 else "Lecturer"
        faculty_rows.append([f"E{i}", f"Teacher {i:02d}", f"t{i}@uni.edu", dept, designation])
    assert run_import(admin_client, "faculty", faculty_rows).result["created"] == 12
    Faculty.objects.filter(employee_id="E11").update(is_exempt=True)

    period = ExamPeriod.objects.create(name="Winter Finals", start_date=date(2026, 12, 1), end_date=date(2026, 12, 5))
    schedule = [SCHEDULE_HEADER]
    for day in range(1, 4):
        for start, end in (("09:00", "12:00"), ("14:00", "17:00")):
            schedule.append([f"2026-12-0{day}", start, end, f"PH{day}{start[:2]}", "Physics", "Physics", "R1", 45, "t1@uni.edu"])
            schedule.append([f"2026-12-0{day}", start, end, f"MA{day}{start[:2]}", "Maths", "Maths", "R2", 30, ""])
    assert run_import(admin_client, "schedule", schedule, period=period).result["created"] == 12

    admin_client.post(reverse("run_allocation", args=[period.pk]))
    assert Duty.objects.filter(period=period).count() == 24
    assert_roster_invariants(period)

    admin_client.post(reverse("publish", args=[period.pk]), {"acknowledge_unfilled": "on", "confirm_rules": "on"})
    period.refresh_from_db()
    assert period.status == PeriodStatus.PUBLISHED

    pdf = admin_client.get(reverse("roster_pdf", args=[period.pk]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert {"allocation.run", "period.publish"} <= set(AuditLog.objects.values_list("action", flat=True))


def test_demo_data_allocates_quickly_and_correctly(admin_user):
    call_command("load_demo")
    period = ExamPeriod.objects.get(name="Demo Finals")
    assert Faculty.objects.count() == 200
    started = clock.perf_counter()
    result = run_allocation(period, admin_user, seed=1)
    elapsed = clock.perf_counter() - started
    assert elapsed < 10, f"allocation took {elapsed:.1f}s"
    assert len(result.assignments) > 1000
    assert_roster_invariants(period)
    capped = period.category_rules.exclude(max_duties=None).values_list("category_id", flat=True)
    totals = Counter(Duty.objects.active().filter(period=period).values_list("faculty_id", flat=True))
    counts = [totals[pk] for pk in Faculty.objects.eligible().exclude(category_id__in=capped).values_list("pk", flat=True)]
    assert max(counts) - min(counts) <= 1, f"default-cap loads range {min(counts)}..{max(counts)}"
    with pytest.raises(CommandError, match="Demo Finals already exists."):
        call_command("load_demo")
