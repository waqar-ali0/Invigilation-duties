import pytest
from django.urls import reverse

from duties.models import Duty

pytestmark = pytest.mark.django_db


def test_run_allocation_post_creates_duties_and_redirects(admin_client, small_period):
    r = admin_client.post(reverse("run_allocation", args=[small_period.pk]))
    assert r.status_code == 302 and r.url == reverse("period_dashboard", args=[small_period.pk])
    assert Duty.objects.filter(period=small_period).count() == 6


def test_run_allocation_get_not_allowed(admin_client, small_period):
    assert admin_client.get(reverse("run_allocation", args=[small_period.pk])).status_code == 405


def test_clear_draft_post(admin_client, small_period):
    admin_client.post(reverse("run_allocation", args=[small_period.pk]))
    admin_client.post(reverse("clear_draft", args=[small_period.pk]))
    assert not Duty.objects.exists()


def test_actions_on_published_period_show_locked_message(admin_client, published_period):
    for name in ("run_allocation", "clear_draft"):
        r = admin_client.post(reverse(name, args=[published_period.pk]), follow=True)
        assert "This exam period is published" in r.content.decode()
    assert Duty.objects.filter(period=published_period).count() == 6
