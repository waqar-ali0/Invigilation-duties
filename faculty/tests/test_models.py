import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from faculty.models import Faculty

pytestmark = pytest.mark.django_db


def test_eligible_excludes_inactive_and_exempt(make_faculty):
    a = make_faculty("A")
    make_faculty("B", is_exempt=True)
    make_faculty("C", is_active=False)
    assert list(Faculty.objects.eligible()) == [a]


def test_department_cannot_be_deleted_with_faculty(dept, make_faculty):
    make_faculty()
    with pytest.raises(ProtectedError):
        dept.delete()


def test_employee_id_unique(make_faculty):
    make_faculty(employee_id="X1")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_faculty(employee_id="X1")


def test_email_unique(make_faculty):
    make_faculty(email="same@uni.edu")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_faculty(email="same@uni.edu")


def test_str_shows_department(make_faculty):
    assert str(make_faculty("Ayesha Khan")) == "Ayesha Khan (Computer Science)"
