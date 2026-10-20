import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from accounts.models import Role, User
from accounts.permissions import role_required
from accounts.signals import EXAM_OFFICE_GROUP

pytestmark = pytest.mark.django_db


def test_create_user_uses_email_and_defaults_to_faculty():
    u = User.objects.create_user("a@uni.edu", "pw12345!")
    assert u.email == "a@uni.edu" and u.role == Role.FACULTY and not u.is_staff


def test_admin_role_is_staff_and_in_exam_office_group():
    u = User.objects.create_user("m@uni.edu", "pw12345!", role=Role.ADMIN)
    assert u.is_staff
    assert u.groups.filter(name=EXAM_OFFICE_GROUP).exists()


def test_exam_office_group_cannot_change_audit():
    g = Group.objects.get(name=EXAM_OFFICE_GROUP)
    codenames = set(g.permissions.values_list("codename", flat=True))
    assert "change_auditlog" not in codenames and "delete_auditlog" not in codenames


def test_role_required_blocks_other_roles():
    @role_required(Role.ADMIN)
    def view(request):
        return "ok"

    req = RequestFactory().get("/")
    req.user = User.objects.create_user("f@uni.edu", "pw12345!")
    with pytest.raises(PermissionDenied):
        view(req)


def test_role_required_allows_listed_role_and_superuser():
    @role_required(Role.ADMIN)
    def view(request):
        return "ok"

    req = RequestFactory().get("/")
    req.user = User.objects.create_user("m@uni.edu", "pw12345!", role=Role.ADMIN)
    assert view(req) == "ok"
    req.user = User.objects.create_superuser("s@uni.edu", "pw12345!")
    assert view(req) == "ok"


def test_role_required_redirects_anonymous():
    @role_required(Role.ADMIN)
    def view(request):
        return "ok"

    req = RequestFactory().get("/x/")
    req.user = AnonymousUser()
    assert view(req).status_code == 302
