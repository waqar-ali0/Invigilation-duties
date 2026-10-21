import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from duties.models import Duty, DutySource, DutyStatus
from exams.models import ExamPeriod, PeriodStatus, TimeSlot

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    client.force_login(User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF))
    return client


@pytest.fixture
def today_duty(period, make_room, make_member):
    """A published duty in a slot that started at 00:00 today."""
    owner = make_member("Present Person")
    room = make_room(day=1, start="00:00", end="23:59", room="T1")
    TimeSlot.objects.filter(pk=room.session.slot_id).update(date=timezone.localdate())
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    return Duty.objects.create(faculty=owner, room_allocation=room, source=DutySource.AUTO)


def test_staff_home_redirects_to_attendance(staff_client):
    r = staff_client.get(reverse("staff_home"))
    assert r.status_code == 302 and r.url == reverse("attendance")


def test_attendance_lists_todays_slots(staff_client, today_duty):
    page = staff_client.get(reverse("attendance")).content.decode()
    assert reverse("attendance_slot", args=[today_duty.room_allocation.session.slot_id]) in page


def test_slot_page_and_marking(staff_client, today_duty):
    slot_id = today_duty.room_allocation.session.slot_id
    page = staff_client.get(reverse("attendance_slot", args=[slot_id])).content.decode()
    assert "Present Person" in page and "T1" in page
    staff_client.post(reverse("attendance_mark", args=[today_duty.pk]), {"present": "0"})
    today_duty.refresh_from_db()
    assert today_duty.status == DutyStatus.ABSENT


def test_mark_room_present_view(staff_client, today_duty):
    staff_client.post(reverse("attendance_room_present", args=[today_duty.room_allocation_id]))
    today_duty.refresh_from_db()
    assert today_duty.status == DutyStatus.COMPLETED


def test_attendance_error_message(staff_client, period, make_room, make_member):
    duty = Duty.objects.create(faculty=make_member(), room_allocation=make_room(day=3), source=DutySource.AUTO)
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    r = staff_client.post(reverse("attendance_mark", args=[duty.pk]), {"present": "1"}, follow=True)
    assert "Attendance can be marked once the exam has started." in r.content.decode()


def test_other_date(staff_client, today_duty):
    page = staff_client.get(reverse("attendance") + "?date=2026-12-09").content.decode()
    assert reverse("attendance_slot", args=[today_duty.room_allocation.session.slot_id]) not in page


def test_faculty_cannot_open_attendance(client, today_duty):
    client.force_login(today_duty.faculty.user)
    assert client.get(reverse("attendance")).status_code == 403
    assert client.post(reverse("attendance_mark", args=[today_duty.pk]), {"present": "1"}).status_code == 403


def test_admin_can_open_attendance(admin_client, today_duty):
    assert admin_client.get(reverse("attendance")).status_code == 200


def test_draft_slots_not_listed(staff_client, today_duty, period):
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.ALLOCATING)
    assert staff_client.get(reverse("attendance_slot", args=[today_duty.room_allocation.session.slot_id])).status_code == 404
