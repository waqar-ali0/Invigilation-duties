import pytest
from django.urls import reverse

from accounts.models import Role, User

pytestmark = pytest.mark.django_db


def test_exam_office_can_list_and_add_users(admin_client):
    assert admin_client.get(reverse("admin:accounts_user_changelist")).status_code == 200
    r = admin_client.post(
        reverse("admin:accounts_user_add"),
        {"email": "new.staff@uni.edu", "role": Role.EXAM_STAFF, "password1": "Strong-pass-123", "password2": "Strong-pass-123", "_save": "Save"},
    )
    assert r.status_code == 302
    assert User.objects.get(email="new.staff@uni.edu").role == Role.EXAM_STAFF


def test_exam_office_cannot_see_or_edit_superusers(admin_client, superuser):
    assert superuser.email not in admin_client.get(reverse("admin:accounts_user_changelist")).content.decode()
    assert admin_client.get(reverse("admin:accounts_user_change", args=[superuser.pk])).status_code in (302, 403, 404)


def test_exam_office_cannot_grant_superuser(admin_client):
    target = User.objects.create_user("t@uni.edu", "x")
    page = admin_client.get(reverse("admin:accounts_user_change", args=[target.pk])).content.decode()
    assert 'name="is_superuser"' not in page and 'name="user_permissions"' not in page and 'name="groups"' not in page
    data = {
        "email": "t@uni.edu",
        "first_name": "",
        "last_name": "",
        "role": Role.FACULTY,
        "is_active": "on",
        "is_superuser": "on",
        "last_login_0": "",
        "last_login_1": "",
        "date_joined_0": "2026-10-21",
        "date_joined_1": "10:00:00",
        "_save": "Save",
    }
    admin_client.post(reverse("admin:accounts_user_change", args=[target.pk]), data)
    target.refresh_from_db()
    assert not target.is_superuser


def test_superuser_still_sees_everything(client, superuser):
    client.force_login(superuser)
    page = client.get(reverse("admin:accounts_user_change", args=[superuser.pk])).content.decode()
    assert 'name="is_superuser"' in page


def test_duty_page_user_links_open_for_exam_office(admin_client, room, room_fac, admin_user):
    from duties.models import Duty, DutySource

    duty = Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.AUTO, attendance_marked_by=admin_user)
    assert admin_client.get(reverse("admin:accounts_user_change", args=[admin_user.pk])).status_code == 200
    assert admin_client.get(reverse("admin:duties_duty_change", args=[duty.pk])).status_code == 200
