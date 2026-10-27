from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from allocation.services import publish_period, run_allocation
from duties.models import Duty, DutyStatus
from exams.lifecycle import LifecycleError, archive, cancel, complete, unarchive
from exams.models import ExamPeriod, ExamType, PeriodLockedError, PeriodStatus
from notifications.models import OutboundEmail

pytestmark = pytest.mark.django_db


def test_lifecycle_complete_archive_unarchive(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    with pytest.raises(LifecycleError, match="run until"):
        complete(small_period, admin_user)  # exams in Dec 2026 are still ahead
    ExamPeriod.objects.filter(pk=small_period.pk).update(end_date=timezone.localdate() - timedelta(days=1), start_date=timezone.localdate() - timedelta(days=5))
    small_period = complete(small_period, admin_user)
    assert small_period.status == PeriodStatus.CLOSED and small_period.get_status_display() == "Completed"
    with pytest.raises(PeriodLockedError):
        small_period.ensure_editable()
    assert archive(small_period, admin_user).status == PeriodStatus.ARCHIVED
    assert unarchive(small_period, admin_user).status == PeriodStatus.CLOSED


def test_in_progress_label(period):
    period.status = PeriodStatus.PUBLISHED
    period.start_date = timezone.localdate() - timedelta(days=1)
    period.end_date = timezone.localdate() + timedelta(days=1)
    assert period.in_progress and period.stage_label == "In progress"


def test_cancel_draft_needs_reason(period, admin_user):
    with pytest.raises(LifecycleError, match="reason"):
        cancel(period, admin_user, "")
    assert cancel(period, admin_user, "Exams postponed").status == PeriodStatus.CANCELLED


def test_cancel_published_keeps_duties_on_record_and_notifies(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    before = OutboundEmail.objects.count()
    cancel(small_period, admin_user, "Strike")
    assert not Duty.objects.filter(period=small_period).exclude(status=DutyStatus.CANCELLED).exists()
    assert Duty.objects.filter(period=small_period).exists()  # kept, not deleted
    assert OutboundEmail.objects.count() > before
    with pytest.raises(LifecycleError):
        cancel(small_period, admin_user, "again")


def test_pages_details_status_and_list_filters(admin_client, period, dept):
    final = ExamType.objects.get(name="Final")
    r = admin_client.post(reverse("period_details", args=[period.pk]), {
        "name": period.name, "term": "Fall 2026", "exam_type": final.pk, "start_date": "2026-12-01",
        "end_date": "2026-12-15", "departments": [dept.pk], "remarks": "Main campus only",
    })
    assert r.status_code == 302
    period.refresh_from_db()
    assert (period.term, period.exam_type, list(period.departments.all()), period.remarks) == ("Fall 2026", final, [dept], "Main campus only")
    page = admin_client.get(reverse("period_dashboard", args=[period.pk])).content.decode()
    assert "Cancel period" in page and "Fall 2026" in page
    admin_client.post(reverse("period_status", args=[period.pk]), {"action": "cancel", "reason": "Postponed"})
    assert "Postponed" in admin_client.get(reverse("period_dashboard", args=[period.pk])).content.decode()
    assert period.name not in admin_client.get(reverse("period_list")).content.decode().split("<main", 1)[1]
    assert period.name in admin_client.get(reverse("period_list"), {"show": "cancelled"}).content.decode()