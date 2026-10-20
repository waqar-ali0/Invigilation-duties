import pytest

from accounts.invitations import InvitationError, activation_url, invite, invite_all, role_for
from accounts.models import Role
from audit.models import AuditLog
from notifications.models import EmailKind, OutboundEmail

pytestmark = pytest.mark.django_db


def test_invite_creates_linked_user_and_queues_email(make_faculty, admin_user, settings):
    settings.SITE_URL = "https://duties.uni.edu"
    f = make_faculty("Ali Khan", email="Ali@Uni.edu")
    user = invite(f, admin_user)
    f.refresh_from_db()
    assert f.user == user and user.email == "ali@uni.edu" and not user.has_usable_password()
    assert user.role == Role.FACULTY and f.invited_at is not None
    email = OutboundEmail.objects.get(kind=EmailKind.ACTIVATION)
    assert email.to == "ali@uni.edu" and activation_url(user) in email.body
    assert activation_url(user).startswith("https://duties.uni.edu/activate/")
    assert "Ali Khan" in email.body
    assert AuditLog.objects.filter(action="account.invite", object_id=str(f.pk)).exists()


def test_invite_gives_hod_role(dept, make_faculty, admin_user):
    f = make_faculty()
    dept.hod = f
    dept.save()
    assert role_for(f) == Role.HOD
    assert invite(f, admin_user).role == Role.HOD


def test_resend_to_active_user_refused(make_faculty, admin_user):
    f = make_faculty("Sara")
    user = invite(f, admin_user)
    user.set_password("Strong-pass-123")
    user.save()
    with pytest.raises(InvitationError, match="Sara already has an active account."):
        invite(f, admin_user)


def test_resend_to_invited_user_sends_new_email(make_faculty, admin_user):
    f = make_faculty()
    first = invite(f, admin_user)
    assert invite(f, admin_user) == first
    assert OutboundEmail.objects.filter(kind=EmailKind.ACTIVATION).count() == 2


def test_invite_inactive_refused(make_faculty, admin_user):
    f = make_faculty("Gone", is_active=False)
    with pytest.raises(InvitationError, match="Gone is inactive."):
        invite(f, admin_user)


def test_invite_all_skips_invited_and_inactive(make_faculty, admin_user):
    a = make_faculty()
    make_faculty(is_active=False)
    invite(a, admin_user)
    make_faculty()
    make_faculty(is_exempt=True)
    assert invite_all(admin_user) == 2
    assert invite_all(admin_user) == 0
