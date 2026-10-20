from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import Role
from audit.models import AuditLog
from duties.models import Unavailability, UnavailabilityStatus
from duties.unavailability import RequestRejected, can_decide, decide_request
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus
from faculty.models import Department
from notifications.models import EmailKind, OutboundEmail

pytestmark = pytest.mark.django_db
D = date(2026, 12, 2)


@pytest.fixture
def hod(dept, make_member):
    h = make_member("Head", role=Role.HOD)
    dept.hod = h
    dept.save()
    h.user.refresh_from_db()
    return h


def request_for(faculty, period, **extra):
    return Unavailability.objects.create(faculty=faculty, period=period, date=D, reason="x", **extra)


def test_hod_decides_own_department(period, hod, make_member):
    req = request_for(make_member(), period)
    assert can_decide(hod.user, req)
    decide_request(req, hod.user, approve=True, comment="OK")
    req.refresh_from_db()
    assert (req.status, req.reviewed_by, req.review_comment) == (UnavailabilityStatus.APPROVED, hod.user, "OK")
    assert req.reviewed_at is not None
    assert AuditLog.objects.filter(action="unavailability.approve").exists()
    email = OutboundEmail.objects.get(kind=EmailKind.REQUEST_DECISION)
    assert email.to == req.faculty.user.email and "approved" in email.body.lower()


def test_hod_cannot_decide_own_request(period, hod):
    req = request_for(hod, period)
    assert not can_decide(hod.user, req)
    with pytest.raises(PermissionDenied):
        decide_request(req, hod.user, approve=True)


def test_hod_cannot_decide_other_department(period, hod, make_member):
    physics = Department.objects.create(name="Physics")
    req = request_for(make_member(department=physics), period)
    assert not can_decide(hod.user, req)


def test_hod_of_two_departments_decides_both(period, hod, make_member):
    physics = Department.objects.create(name="Physics", hod=hod)
    req = request_for(make_member(department=physics), period)
    assert can_decide(hod.user, req)


def test_exempt_hod_still_decides(period, hod, make_member):
    hod.is_exempt = True
    hod.save()
    assert can_decide(hod.user, request_for(make_member(), period))


def test_faculty_cannot_decide(period, make_member):
    assert not can_decide(make_member().user, request_for(make_member(), period))


def test_admin_decides_hod_request_and_rejection_email(period, hod, admin_user):
    req = request_for(hod, period)
    assert can_decide(admin_user, req)
    decide_request(req, admin_user, approve=False, comment="Exams need you")
    req.refresh_from_db()
    assert req.status == UnavailabilityStatus.REJECTED
    assert "Exams need you" in OutboundEmail.objects.get(kind=EmailKind.REQUEST_DECISION).body


def test_decided_twice_rejected(period, hod, make_member):
    req = request_for(make_member(), period)
    decide_request(req, hod.user, approve=True)
    with pytest.raises(RequestRejected) as e:
        decide_request(req, hod.user, approve=False)
    assert e.value.errors == ["This request was already decided."]


def test_decision_on_published_period_locked(period, hod, make_member):
    req = request_for(make_member(), period)
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    with pytest.raises(PeriodLockedError):
        decide_request(req, hod.user, approve=True)


def test_decision_allowed_after_window_closed(period, hod, make_member):
    ExamPeriod.objects.filter(pk=period.pk).update(
        status=PeriodStatus.AVAILABILITY_OPEN, availability_deadline=timezone.now() - timedelta(hours=1)
    )
    req = request_for(make_member(), period)
    decide_request(req, hod.user, approve=True)
