import re

import pytest
from django.conf import settings
from django.urls import reverse

from accounts.invitations import activation_url, invite
from notifications.models import EmailKind, OutboundEmail

pytestmark = pytest.mark.django_db


def local(url):
    return url.replace(settings.SITE_URL, "")


def test_activation_link_sets_password_and_logs_in(client, make_faculty, admin_user):
    user = invite(make_faculty(), admin_user)
    url = local(activation_url(user))
    assert client.get(url).status_code == 200
    r = client.post(url, {"new_password1": "Strong-pass-123", "new_password2": "Strong-pass-123"})
    assert r.status_code == 302
    user.refresh_from_db()
    assert user.has_usable_password() and user.check_password("Strong-pass-123")
    assert client.session["_auth_user_id"] == str(user.pk)


def test_activation_link_works_only_once(client, make_faculty, admin_user):
    user = invite(make_faculty(), admin_user)
    url = local(activation_url(user))
    client.post(url, {"new_password1": "Strong-pass-123", "new_password2": "Strong-pass-123"})
    client.logout()
    assert "invalid or has expired" in client.get(url).content.decode()


def test_bad_activation_link_shows_message(client):
    r = client.get(reverse("activate", args=["MQ", "bad-token"]))
    assert r.status_code == 200
    assert "This link is invalid or has expired. Ask the exam office to resend it." in r.content.decode()


def test_password_reset_goes_through_outbox(client, make_faculty, admin_user):
    user = invite(make_faculty(email="reset@uni.edu"), admin_user)
    user.set_password("Old-pass-123")
    user.save()
    client.post(reverse("password_reset"), {"email": "reset@uni.edu"})
    email = OutboundEmail.objects.get(kind=EmailKind.PASSWORD_RESET)
    assert email.to == "reset@uni.edu"
    link = re.search(r"https?://\S+", email.body).group(0)
    r = client.get(local(link), follow=True)
    assert r.status_code == 200 and b"new_password1" in r.content


def test_login_page_links_to_password_reset(client):
    assert reverse("password_reset") in client.get(reverse("login")).content.decode()


def test_accounts_page_admin_only(client, faculty_user):
    client.force_login(faculty_user)
    assert client.get(reverse("accounts_page")).status_code == 403


def test_accounts_page_groups_and_invite_buttons(admin_client, make_faculty, admin_user):
    make_faculty("Never Invited")
    invited = make_faculty("Invited Person")
    invite(invited, admin_user)
    page = admin_client.get(reverse("accounts_page")).content.decode()
    assert "Never Invited" in page and "Invited Person" in page and "No account (1)" in page
    admin_client.post(reverse("invite_all"))
    assert OutboundEmail.objects.filter(kind=EmailKind.ACTIVATION).count() == 2


def test_invite_one_shows_error_for_active_account(admin_client, make_faculty, admin_user):
    f = make_faculty("Done")
    user = invite(f, admin_user)
    user.set_password("Strong-pass-123")
    user.save()
    r = admin_client.post(reverse("invite_one", args=[f.pk]), follow=True)
    assert "Done already has an active account." in r.content.decode()
