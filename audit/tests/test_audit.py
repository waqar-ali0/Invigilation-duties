import pytest
from django.contrib.admin.sites import site

from audit.log import log
from audit.models import AuditLog

pytestmark = pytest.mark.django_db


def test_log_records_object_and_actor(admin_user, room):
    entry = log(admin_user, "duty.assign", room, after={"x": 1})
    assert (entry.object_type, entry.object_id, entry.after, entry.actor) == (
        "exams.RoomAllocation",
        str(room.pk),
        {"x": 1},
        admin_user,
    )


def test_log_allows_system_actor(room):
    assert log(None, "allocation.run", room).actor is None


def test_audit_admin_denies_add_change_and_delete_even_for_superuser(rf, superuser, room):
    entry = log(superuser, "duty.assign", room)
    model_admin = site._registry[AuditLog]
    request = rf.get("/")
    request.user = superuser
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, entry)
    assert not model_admin.has_delete_permission(request, entry)
    assert model_admin.has_view_permission(request, entry)
