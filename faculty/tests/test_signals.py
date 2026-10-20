import pytest

from accounts.models import Role
from exams.importers.faculty import import_faculty
from exams.tests.helpers import FACULTY_HEADER, make_xlsx
from faculty.models import Department

pytestmark = pytest.mark.django_db


def test_setting_hod_promotes_and_replacing_demotes(dept, make_member):
    a, b = make_member(), make_member()
    dept.hod = a
    dept.save()
    a.user.refresh_from_db()
    assert a.user.role == Role.HOD
    dept.hod = b
    dept.save()
    a.user.refresh_from_db()
    b.user.refresh_from_db()
    assert (a.user.role, b.user.role) == (Role.FACULTY, Role.HOD)


def test_hod_of_two_departments_keeps_role_when_leaving_one(dept, make_member):
    other = Department.objects.create(name="Physics")
    a = make_member()
    dept.hod = a
    dept.save()
    other.hod = a
    other.save()
    dept.hod = None
    dept.save()
    a.user.refresh_from_db()
    assert a.user.role == Role.HOD


def test_hod_sync_leaves_admin_role_alone(dept, make_member):
    a = make_member(role=Role.ADMIN)
    dept.hod = a
    dept.save()
    a.user.refresh_from_db()
    assert a.user.role == Role.ADMIN


def test_faculty_email_change_updates_user(make_member):
    f = make_member(email="old@uni.edu")
    f.email = "New@Uni.edu"
    f.save()
    f.user.refresh_from_db()
    assert f.user.email == "new@uni.edu"


def test_import_update_syncs_user_email(dept, make_member):
    f = make_member(employee_id="E1", email="old@uni.edu")
    res = import_faculty(make_xlsx([FACULTY_HEADER, ["E1", f.name, "fresh@uni.edu", dept.name, "Lecturer"]]))
    assert res.ok
    f.user.refresh_from_db()
    assert f.user.email == "fresh@uni.edu"


def test_deactivating_faculty_deactivates_user(make_member):
    f = make_member()
    f.is_active = False
    f.save()
    f.user.refresh_from_db()
    assert not f.user.is_active
    f.is_active = True
    f.save()
    f.user.refresh_from_db()
    assert f.user.is_active
