from datetime import date

import pytest
from django.core.management import call_command

from exams.models import ExamPeriod, RoomAllocation
from faculty.models import Faculty

pytestmark = pytest.mark.django_db


def test_options_set_name_start_and_size():
    call_command("load_demo", "--name", "Small Demo", "--start", "2027-01-11", "--courses", "3")
    period = ExamPeriod.objects.get(name="Small Demo")
    assert (period.start_date, period.end_date) == (date(2027, 1, 11), date(2027, 1, 20))
    assert RoomAllocation.objects.filter(session__slot__period=period).count() == 10 * 2 * 3 * 2


def test_second_demo_period_reuses_faculty():
    call_command("load_demo", "--courses", "2")
    call_command("load_demo", "--name", "Second Demo", "--start", "2027-01-11", "--courses", "2")
    assert Faculty.objects.count() == 200
    assert ExamPeriod.objects.count() == 2
