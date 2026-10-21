import pytest
from django.urls import reverse

from exams.models import ExamPeriod

pytestmark = pytest.mark.django_db


def period_form(**settings):
    data = {
        "name": "New Finals",
        "start_date": "2027-03-01",
        "end_date": "2027-03-05",
        "availability_deadline_0": "",
        "availability_deadline_1": "",
        "settings-TOTAL_FORMS": "1",
        "settings-INITIAL_FORMS": "0",
        "settings-MIN_NUM_FORMS": "0",
        "settings-MAX_NUM_FORMS": "1",
        "settings-0-max_duties_per_faculty": "8",
        "settings-0-max_duties_per_day": "2",
        "settings-0-students_per_invigilator": "30",
        "settings-0-min_invigilators_per_room": "2",
        "settings-0-avoid_back_to_back": "on",
        "settings-0-exclude_course_teacher": "on",
        "settings-0-swap_cutoff_hours": "24",
        "designation_caps-TOTAL_FORMS": "0",
        "designation_caps-INITIAL_FORMS": "0",
        "designation_caps-MIN_NUM_FORMS": "0",
        "designation_caps-MAX_NUM_FORMS": "1000",
        "_save": "Save",
    }
    data.update({f"settings-0-{k}": v for k, v in settings.items()})
    return data


def test_create_period_in_admin_with_changed_settings(admin_client):
    r = admin_client.post(reverse("admin:exams_examperiod_add"), period_form(max_duties_per_faculty="4", swap_cutoff_hours="48"))
    assert r.status_code == 302
    period = ExamPeriod.objects.get(name="New Finals")
    assert (period.settings.max_duties_per_faculty, period.settings.swap_cutoff_hours) == (4, 48)


def test_create_period_in_admin_with_default_settings(admin_client):
    r = admin_client.post(reverse("admin:exams_examperiod_add"), period_form())
    assert r.status_code == 302
    assert ExamPeriod.objects.get(name="New Finals").settings.max_duties_per_faculty == 8
