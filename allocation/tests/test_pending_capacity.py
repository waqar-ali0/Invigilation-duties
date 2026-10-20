from datetime import date

import pytest
from django.urls import reverse

from allocation.services import capacity_report, pending_capacity
from duties.models import Unavailability, UnavailabilityStatus

pytestmark = pytest.mark.django_db


def test_no_pending_means_none(short_period):
    assert pending_capacity(short_period) is None


def test_pending_counted_as_unavailable(short_period):
    from faculty.models import Faculty

    Unavailability.objects.create(faculty=Faculty.objects.first(), period=short_period, date=date(2026, 12, 1), reason="x")
    count, report = pending_capacity(short_period)
    assert count == 1
    assert report.capacity == 1 and capacity_report(short_period).capacity == 2
    assert report.slot_shortages == [(short_period.slots.get().id, 5, 1)]


def test_approved_requests_not_counted_as_pending(short_period):
    from faculty.models import Faculty

    Unavailability.objects.create(
        faculty=Faculty.objects.first(), period=short_period, date=date(2026, 12, 1), reason="x", status=UnavailabilityStatus.APPROVED
    )
    assert pending_capacity(short_period) is None


def test_dashboard_shows_pending_warning(admin_client, short_period):
    from faculty.models import Faculty

    Unavailability.objects.create(faculty=Faculty.objects.first(), period=short_period, date=date(2026, 12, 1), reason="x")
    page = admin_client.get(reverse("period_dashboard", args=[short_period.pk])).content.decode()
    assert "1 request(s) pending. If all are approved, capacity drops to 1 duties (need 5)." in page
