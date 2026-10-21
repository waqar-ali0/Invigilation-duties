from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from audit.log import log
from audit.models import AuditLog
from audit.views import describe_changes
from duties.models import Duty, DutySource
from exams.models import ExamPeriod

pytestmark = pytest.mark.django_db


def test_describe_changes():
    entry = AuditLog(before={"status": "assigned", "faculty": 3}, after={"status": "absent", "reason": "Sick"})
    assert describe_changes(entry) == ["faculty: 3 → —", "reason: — → Sick", "status: assigned → absent"]
    assert describe_changes(AuditLog()) == []


def test_viewer_admin_only(client, faculty_user):
    client.force_login(faculty_user)
    assert client.get(reverse("audit_log")).status_code == 403


def test_viewer_is_read_only(admin_client):
    assert admin_client.post(reverse("audit_log")).status_code == 405


def test_filters(admin_client, admin_user, room, make_faculty, period):
    other_user = User.objects.create_user("other@uni.edu", "x", role=Role.ADMIN)
    duty = Duty.objects.create(faculty=make_faculty(), room_allocation=room, source=DutySource.AUTO)
    log(admin_user, "duty.replace", duty, after={"reason": "Sick"})
    log(other_user, "period.publish", period)
    old = log(admin_user, "allocation.run", period)
    AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=10))
    other_period = ExamPeriod.objects.create(name="Other", start_date=date(2027, 1, 1), end_date=date(2027, 1, 5))
    log(admin_user, "allocation.run", other_period)

    def actions(**params):
        page = admin_client.get(reverse("audit_log"), params).context["page"]
        return sorted(e.action for e in page.object_list)

    assert actions(action="duty.replace") == ["duty.replace"]
    assert actions(actor="other@") == ["period.publish"]
    assert actions(date_from=(timezone.localdate() - timedelta(days=1)).isoformat(), period=period.pk) == [
        "duty.replace",
        "period.publish",
    ]
    assert actions(period=other_period.pk) == ["allocation.run"]
    assert "reason: — → Sick" in admin_client.get(reverse("audit_log"), {"action": "duty.replace"}).content.decode()


def test_invalid_dates_ignored_with_message(admin_client, admin_user, period):
    log(admin_user, "allocation.run", period)
    r = admin_client.get(reverse("audit_log"), {"date_from": "not-a-date"})
    assert len(r.context["page"].object_list) == 1 and "Ignored an invalid date." in r.content.decode()


def test_pagination(admin_client, admin_user, period):
    for _ in range(60):
        log(admin_user, "allocation.run", period)
    first = admin_client.get(reverse("audit_log")).context["page"]
    second = admin_client.get(reverse("audit_log"), {"page": 2}).context["page"]
    assert (len(first.object_list), len(second.object_list), first.paginator.num_pages) == (50, 10, 2)
