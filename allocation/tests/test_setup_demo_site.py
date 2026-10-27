import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Role, User
from exams.models import ExamPeriod, PeriodStatus
from faculty.models import Department

pytestmark = pytest.mark.django_db


def test_demo_site_needs_a_password(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="DEMO_PASSWORD"):
        call_command("setup_demo_site")


def test_demo_site_builds_once_and_logins_work(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "Demo-pass-123456")
    call_command("setup_demo_site")
    call_command("setup_demo_site")  # second start: nothing duplicated
    assert ExamPeriod.objects.get(name="Demo Finals").status == PeriodStatus.PUBLISHED
    assert ExamPeriod.objects.filter(name="Demo Midterms").count() == 1
    hod = User.objects.get(email="demo.hod@uni.edu")
    assert hod.role == Role.HOD and hod.check_password("Demo-pass-123456")
    assert Department.objects.get(hod=hod.faculty).name == "Department 01"
    assert User.objects.get(email="demo.faculty@uni.edu").faculty.duties.exists()


def test_demo_banner_only_on_demo_sites(client, settings):
    settings.DEMO_SITE = False
    assert "Demo logins" not in client.get("/login/").content.decode()
    settings.DEMO_SITE = True
    page = client.get("/login/").content.decode()
    assert "Demo logins" in page and "sample data" in page
