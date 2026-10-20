from datetime import date

import pytest
from django.urls import reverse

from duties.models import Duty
from exams.models import ExamPeriod, PeriodStatus, RoomAllocation
from faculty.models import Faculty

pytestmark = pytest.mark.django_db


def room_url(name, room, *extra):
    return reverse(name, args=[room.session.slot.period_id, room.pk, *extra])


def test_room_detail_lists_candidates(admin_client, small_period):
    room = RoomAllocation.objects.first()
    page = admin_client.get(room_url("room_detail", room)).content.decode()
    assert "Teacher 0" in page and "0 duties" in page


def test_add_duty_success(admin_client, small_period):
    room = RoomAllocation.objects.first()
    fac = Faculty.objects.first()
    r = admin_client.post(room_url("add_duty", room), {"faculty_id": fac.pk})
    assert r.status_code == 302 and Duty.objects.filter(faculty=fac, room_allocation=room).exists()


def test_add_duty_blocked_shows_error(admin_client, make_room, make_faculty, period):
    a1 = make_room(course="CS101", room="A1")
    a2 = make_room(course="CS102", room="A2")
    fac = make_faculty()
    admin_client.post(room_url("add_duty", a1), {"faculty_id": fac.pk})
    r = admin_client.post(room_url("add_duty", a2), {"faculty_id": fac.pk}, follow=True)
    assert "Already assigned in this time slot" in r.content.decode()
    assert not Duty.objects.filter(room_allocation=a2).exists()


def test_add_duty_with_warning_requires_confirm(admin_client, room, make_faculty):
    fac = make_faculty()
    room.session.course_teachers.add(fac)
    r = admin_client.post(room_url("add_duty", room), {"faculty_id": fac.pk})
    assert r.status_code == 200 and "Teaches this course" in r.content.decode()
    assert "Assign anyway" in r.content.decode()
    assert not Duty.objects.exists()
    admin_client.post(room_url("add_duty", room), {"faculty_id": fac.pk, "confirm": "1"})
    assert Duty.objects.filter(faculty=fac).exists()


def test_remove_duty(admin_client, room, make_faculty):
    fac = make_faculty()
    admin_client.post(room_url("add_duty", room), {"faculty_id": fac.pk})
    duty = Duty.objects.get()
    admin_client.post(reverse("remove_duty", args=[room.session.slot.period_id, duty.pk]))
    assert not Duty.objects.exists()


def test_room_from_other_period_404(admin_client, room, make_room):
    other = ExamPeriod.objects.create(name="Other", start_date=date(2026, 12, 1), end_date=date(2026, 12, 15))
    url = reverse("room_detail", args=[other.pk, room.pk])
    assert admin_client.get(url).status_code == 404


def test_publish_page_shows_summary(admin_client, short_period):
    admin_client.post(reverse("run_allocation", args=[short_period.pk]))
    page = admin_client.get(reverse("publish", args=[short_period.pk])).content.decode()
    assert "3 seat(s) are still unfilled" in page and 'name="acknowledge_unfilled"' in page


def test_publish_without_ack_when_unfilled_is_refused(admin_client, short_period):
    admin_client.post(reverse("run_allocation", args=[short_period.pk]))
    r = admin_client.post(reverse("publish", args=[short_period.pk]), follow=True)
    assert "3 seats are unfilled" in r.content.decode()
    short_period.refresh_from_db()
    assert short_period.status != PeriodStatus.PUBLISHED


def test_publish_with_ack_publishes(admin_client, short_period):
    admin_client.post(reverse("run_allocation", args=[short_period.pk]))
    admin_client.post(reverse("publish", args=[short_period.pk]), {"acknowledge_unfilled": "on"})
    short_period.refresh_from_db()
    assert short_period.status == PeriodStatus.PUBLISHED


def test_edit_after_publish_shows_locked_message(admin_client, published_period, make_faculty):
    room = RoomAllocation.objects.first()
    duty = Duty.objects.filter(room_allocation=room).first()
    r = admin_client.post(room_url("add_duty", room), {"faculty_id": make_faculty().pk}, follow=True)
    assert "This exam period is published" in r.content.decode()
    r = admin_client.post(reverse("remove_duty", args=[published_period.pk, duty.pk]), follow=True)
    assert "This exam period is published" in r.content.decode()
    assert Duty.objects.filter(pk=duty.pk).exists()


def test_dashboard_links_rooms(admin_client, small_period):
    admin_client.post(reverse("run_allocation", args=[small_period.pk]))
    room = RoomAllocation.objects.first()
    page = admin_client.get(reverse("period_dashboard", args=[small_period.pk])).content.decode()
    assert room_url("room_detail", room) in page and reverse("publish", args=[small_period.pk]) in page
