import pytest
from django.urls import reverse

from accounts.models import Role, User
from allocation.services import publish_period, run_allocation
from duties.models import Duty, DutySource
from exams.models import ExamPeriod, RoomAllocation

pytestmark = pytest.mark.django_db


def test_login_is_case_insensitive(client, make_member):
    f = make_member(email="ali@uni.edu")
    r = client.post(reverse("login"), {"username": "ALI@Uni.edu", "password": "Strong-pass-123"})
    assert r.status_code == 302 and client.session["_auth_user_id"] == str(f.user.pk)


def test_deactivated_faculty_cannot_log_in(client, make_member):
    f = make_member(email="gone@uni.edu")
    f.is_active = False
    f.save()
    client.post(reverse("login"), {"username": "gone@uni.edu", "password": "Strong-pass-123"})
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize(
    "role,target", [(Role.ADMIN, "period_list"), (Role.FACULTY, "my_duties"), (Role.HOD, "my_duties"), (Role.EXAM_STAFF, "staff_home")]
)
def test_home_redirects_by_role(client, make_member, role, target):
    user = make_member(role=role).user if role in (Role.FACULTY, Role.HOD) else User.objects.create_user(f"{role}@uni.edu", "x", role=role)
    client.force_login(user)
    r = client.get(reverse("home"))
    assert r.status_code == 302 and r.url == reverse(target)


def test_login_redirects_to_home(client, make_member):
    make_member(email="t@uni.edu")
    r = client.post(reverse("login"), {"username": "t@uni.edu", "password": "Strong-pass-123"})
    assert r.url == reverse("home")


def test_staff_home_placeholder(client):
    client.force_login(User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF))
    assert "Attendance marking arrives in Phase 3." in client.get(reverse("staff_home")).content.decode()


def test_unlinked_faculty_user_sees_message(client, faculty_user):
    client.force_login(faculty_user)
    r = client.get(reverse("my_duties"))
    assert r.status_code == 200
    assert "Your account is not linked to a faculty record; contact the exam office." in r.content.decode()


def test_admin_cannot_open_my_duties(admin_client):
    assert admin_client.get(reverse("my_duties")).status_code == 403


def test_my_duties_shows_only_own_published_duties(client, small_period, admin_user, make_member):
    me = make_member("Me Myself")
    other = make_member("Someone Else")
    rooms = list(RoomAllocation.objects.all())
    Duty.objects.create(faculty=me, room_allocation=rooms[0], source=DutySource.MANUAL)
    Duty.objects.create(faculty=other, room_allocation=rooms[1], source=DutySource.MANUAL)
    client.force_login(me.user)
    main = client.get(reverse("my_duties")).content.decode().split("<main>")[1]  # skip the CSRF token in the header
    assert rooms[0].room not in main  # draft: hidden
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, acknowledge_unfilled=True)
    page = client.get(reverse("my_duties")).content.decode()
    assert rooms[0].session.course_code in page and "Fall 2026 Finals" in page


def test_my_duties_lists_rooms_of_own_duties_only(client, period, make_room, make_member, admin_user):
    me, other = make_member(), make_member()
    mine = make_room(room="MINE-1", course="CS1")
    theirs = make_room(room="THEIRS-9", course="CS2")
    Duty.objects.create(faculty=me, room_allocation=mine, source=DutySource.MANUAL)
    Duty.objects.create(faculty=other, room_allocation=theirs, source=DutySource.MANUAL)
    ExamPeriod.objects.filter(pk=period.pk).update(status="published")
    client.force_login(me.user)
    page = client.get(reverse("my_duties")).content.decode()
    assert "MINE-1" in page and "THEIRS-9" not in page
