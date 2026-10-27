import hashlib
import hmac
import json
from datetime import date, datetime, time, timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from duties.handover import replace_duty
from duties.tests.conftest import published_duty  # noqa: F401  (shared fixture)
from duties.models import Duty, DutyStatus
from exams.models import ExamPeriod, PeriodStatus
from messaging import client
from messaging.models import (
    Consent,
    ContactStatus,
    MessageKind,
    MessageStatus,
    MessageTemplate,
    Preference,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppSettings,
)
from messaging.services import apply_status_report, duty_values, eligibility, queue_reminders, send_pending

pytestmark = pytest.mark.django_db
DAY = date(2026, 12, 2)  # the published_duty fixture's exam day


@pytest.fixture
def ready(settings):
    """WhatsApp configured (fake token) with every template active."""
    settings.WHATSAPP_TOKEN = "test-token-not-real"
    settings.WHATSAPP_APP_SECRET = "app-secret"
    settings.WHATSAPP_VERIFY_TOKEN = "verify-me"
    cfg = WhatsAppSettings.get()
    cfg.phone_number_id, cfg.enabled, cfg.report_minutes_before = "123456", True, 15
    cfg.save()
    MessageTemplate.objects.update(active=True)
    return cfg


def opt_in(faculty, phone="0300-1234567"):
    faculty.phone = phone
    faculty.save()
    return WhatsAppContact.objects.create(faculty=faculty, consent=Consent.OPTED_IN, consent_at=timezone.now(), consent_source="test")


def test_eligibility_explains_every_reason(make_faculty):
    f = make_faculty("No Phone")
    assert eligibility(f)[1] == "no valid phone number"
    f.phone = "0300-1111111"
    f.save()
    assert eligibility(f)[1] == "has not opted in"
    c = WhatsAppContact.objects.create(faculty=f, consent=Consent.OPTED_OUT)
    f.refresh_from_db()
    assert eligibility(f)[1] == "opted out"
    c.consent, c.preference = Consent.OPTED_IN, Preference.EMAIL
    c.save()
    f.refresh_from_db()
    assert eligibility(f)[1] == "prefers email only"
    c.preference, c.status = Preference.BOTH, ContactStatus.INVALID
    c.save()
    f.refresh_from_db()
    assert eligibility(f)[1] == "number marked not on WhatsApp"
    c.status = ContactStatus.OK
    c.save()
    f.refresh_from_db()
    assert eligibility(f) == (c, "")


def test_reminders_use_real_duty_data_and_never_duplicate(published_duty, ready):
    opt_in(published_duty.faculty)
    assert queue_reminders(DAY) == (1, [])
    assert queue_reminders(DAY) == (0, [])  # the scheduler running again sends nothing new
    m = WhatsAppMessage.objects.get()
    values = duty_values(published_duty)
    assert m.params == [values[k] for k in MessageTemplate.objects.get(kind="reminder").parameters]
    assert m.params[:4] == ["Owner Person", "Wed 02 Dec 2026", "09:00–12:00", "A1"]
    assert m.params[-1] == "08:45"  # reporting time = start - 15 minutes
    assert m.to == "+923001234567" and "Dear Owner Person" in m.preview and "{" not in m.preview


def test_concurrent_queueing_is_safe(published_duty, ready):
    opt_in(published_duty.faculty)
    from messaging import services

    real = services.WhatsAppMessage.objects.filter

    def as_if_not_there(*args, **kwargs):  # the other run hasn't committed when we look
        qs = real(*args, **kwargs)
        return qs.none() if "key" in kwargs else qs

    WhatsAppMessage.objects.create(key=f"reminder:{published_duty.pk}:{DAY}", kind="reminder", to="+92", template_name="x", language="en", scheduled_for=timezone.now())
    with mock.patch.object(services.WhatsAppMessage.objects, "filter", side_effect=as_if_not_there):
        assert queue_reminders(DAY) == (0, [])
    assert WhatsAppMessage.objects.count() == 1


def test_people_who_cannot_receive_are_reported_not_messaged(published_duty, ready):
    queued, skipped = queue_reminders(DAY)
    assert queued == 0 and skipped[0][1] == "no valid phone number"
    assert not WhatsAppMessage.objects.exists()


def test_cancelled_and_replaced_duties_are_not_reminded(published_duty, ready, admin_user, make_faculty):
    opt_in(published_duty.faculty)
    queue_reminders(DAY)
    newcomer = make_faculty("Newcomer")
    opt_in(newcomer, "0300-7654321")
    replace_duty(published_duty, newcomer, admin_user, "Ill")
    old = WhatsAppMessage.objects.get(kind="reminder", duty=published_duty)
    assert old.status == MessageStatus.CANCELLED
    kinds = set(WhatsAppMessage.objects.exclude(pk=old.pk).values_list("kind", "faculty__name"))
    assert kinds == {(MessageKind.REPLACEMENT, "Newcomer"), (MessageKind.CANCELLED, "Owner Person")}
    queue_reminders(DAY)
    assert WhatsAppMessage.objects.filter(kind="reminder", faculty=newcomer).count() == 1
    assert WhatsAppMessage.objects.filter(kind="reminder", faculty=published_duty.faculty).count() == 1  # only the cancelled one


def test_without_credentials_nothing_is_marked_sent(published_duty, ready, settings):
    opt_in(published_duty.faculty)
    queue_reminders(DAY)
    settings.WHATSAPP_TOKEN = ""
    with mock.patch.object(client, "send_template") as send:
        submitted, failed, note = send_pending()
    send.assert_not_called()
    assert (submitted, failed) == (0, 0) and "not set up" in note
    assert WhatsAppMessage.objects.get().status == MessageStatus.QUEUED


def test_submitted_then_statuses_from_webhook(client, published_duty, ready):
    opt_in(published_duty.faculty)
    queue_reminders(DAY)
    with mock.patch("messaging.services.client.send_template", return_value="wamid.ABC") as send:
        assert send_pending()[:2] == (1, 0)
    cfg_arg, to, name, lang, params = send.call_args.args
    assert to == "+923001234567" and name == "duty_reminder" and lang == "en"
    m = WhatsAppMessage.objects.get()
    assert m.status == MessageStatus.SUBMITTED and m.provider_id == "wamid.ABC" and m.sent_at is None

    def report(status, ts):
        body = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.ABC", "status": status, "timestamp": str(ts)}]}}]}]}).encode()
        sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        return client.post(reverse("whatsapp_webhook"), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)

    now = int(timezone.now().timestamp())
    assert report("delivered", now).status_code == 200
    assert report("sent", now - 5).status_code == 200  # late "sent" never downgrades "delivered"
    m.refresh_from_db()
    assert m.status == MessageStatus.DELIVERED and m.delivered_at is not None
    report("read", now + 5)
    m.refresh_from_db()
    assert m.status == MessageStatus.READ
    assert WhatsAppContact.objects.get().verified


def test_webhook_rejects_bad_signatures_and_verifies_subscription(client, ready):
    body = b'{"entry": []}'
    assert client.post(reverse("whatsapp_webhook"), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256="sha256=bad").status_code == 403
    ok = client.get(reverse("whatsapp_webhook"), {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.content == b"42"
    assert client.get(reverse("whatsapp_webhook"), {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}).status_code == 403


def test_temporary_errors_retry_and_permanent_ones_fail(published_duty, ready):
    opt_in(published_duty.faculty)
    queue_reminders(DAY)
    with mock.patch("messaging.services.client.send_template", side_effect=client.ApiError("busy", code=130429, retry=True)):
        send_pending()
    m = WhatsAppMessage.objects.get()
    assert m.status == MessageStatus.QUEUED and m.attempts == 1 and m.scheduled_for > timezone.now()
    WhatsAppMessage.objects.update(scheduled_for=timezone.now())
    with mock.patch("messaging.services.client.send_template", side_effect=client.ApiError("not on WhatsApp", code=131026, retry=False)):
        assert send_pending()[:2] == (0, 1)
    m.refresh_from_db()
    assert m.status == MessageStatus.FAILED and "not on WhatsApp" in m.error
    assert WhatsAppContact.objects.get().status == ContactStatus.INVALID


def test_inactive_template_means_no_message(published_duty, ready):
    opt_in(published_duty.faculty)
    MessageTemplate.objects.filter(kind="reminder").update(active=False)
    queued, skipped = queue_reminders(DAY)
    assert queued == 0 and skipped[0][1] == "no active template for this kind of message"


def test_scheduler_waits_for_dispatch_time_and_respects_switch(published_duty, ready):
    opt_in(published_duty.faculty)
    day_before = timezone.make_aware(datetime.combine(DAY - timedelta(days=1), time(17, 0)))
    with mock.patch("messaging.services.timezone.now", return_value=day_before), mock.patch("messaging.services.client.send_template", return_value="w1"):
        call_command("whatsapp_tick")
    assert not WhatsAppMessage.objects.exists()  # before 18:00
    with mock.patch("messaging.services.timezone.now", return_value=day_before + timedelta(hours=1, minutes=5)), mock.patch("messaging.services.client.send_template", return_value="w1"):
        call_command("whatsapp_tick")
        call_command("whatsapp_tick")
    assert WhatsAppMessage.objects.get().status == MessageStatus.SUBMITTED
    ready.enabled = False
    ready.save()
    WhatsAppMessage.objects.all().delete()
    call_command("whatsapp_tick")
    assert not WhatsAppMessage.objects.exists()


def test_messaging_centre_previews_then_queues_only_eligible(admin_client, ready, make_faculty):
    a, b = make_faculty("Reachable"), make_faculty("Unreachable", phone="0300-2222222")
    opt_in(a)
    params = {"audience": "people", "faculty": [a.pk, b.pk], "text": "Exams start Monday."}
    page = admin_client.get(reverse("whatsapp_centre"), params).content.decode()
    assert "1</strong> of 2" in page and "has not opted in" in page and not WhatsAppMessage.objects.exists()
    with mock.patch("messaging.services.client.send_template", return_value="w9"):
        admin_client.post(reverse("whatsapp_centre"), {**params, "confirm": "yes"})
    (m,) = WhatsAppMessage.objects.all()
    assert m.faculty == a and m.kind == MessageKind.ANNOUNCEMENT and m.params == ["Reachable", "Exams start Monday."]
    assert m.status == MessageStatus.SUBMITTED


def test_token_never_appears_in_pages(admin_client, ready, published_duty):
    for name in ("whatsapp_settings", "whatsapp_log", "whatsapp_tomorrow", "whatsapp_centre"):
        assert "test-token-not-real" not in admin_client.get(reverse(name)).content.decode()


def test_settings_page_reports_missing_setup(admin_client):
    page = admin_client.get(reverse("whatsapp_settings")).content.decode()
    assert "not set up yet" in page and "WHATSAPP_TOKEN" in page


def test_faculty_opt_in_records_consent(client, make_member):
    member = make_member("Consenting")
    client.force_login(member.user)
    client.post(reverse("my_whatsapp"), {"number": "", "consent": Consent.OPTED_IN, "preference": Preference.BOTH})
    c = WhatsAppContact.objects.get(faculty=member)
    assert c.consent == Consent.OPTED_IN and c.consent_source == "faculty portal" and c.consent_at
    client.post(reverse("my_whatsapp"), {"number": "", "consent": Consent.OPTED_OUT, "preference": Preference.BOTH})
    c.refresh_from_db()
    assert c.consent == Consent.OPTED_OUT


def test_tomorrow_page_and_log_export(admin_client, published_duty, ready):
    opt_in(published_duty.faculty)
    page = admin_client.get(reverse("whatsapp_tomorrow"), {"date": DAY.isoformat()}).content.decode()
    assert "Owner Person" in page and "will be queued" in page
    admin_client.post(reverse("whatsapp_tomorrow") + f"?date={DAY.isoformat()}", {"action": "queue"})
    assert WhatsAppMessage.objects.count() == 1
    csv = admin_client.get(reverse("whatsapp_log"), {"format": "csv"}).content.decode("utf-8-sig")
    assert "Owner Person" in csv and "Queued" in csv


def test_status_report_marks_failures():
    m = WhatsAppMessage.objects.create(kind="reminder", to="+92", template_name="x", language="en", scheduled_for=timezone.now(), provider_id="wamid.F", status=MessageStatus.SUBMITTED)
    apply_status_report({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.F", "status": "failed", "errors": [{"title": "Re-engagement message"}]}]}}]}]})
    m.refresh_from_db()
    assert m.status == MessageStatus.FAILED and m.error == "Re-engagement message"


def test_only_admins_reach_messaging_pages(client, faculty_user):
    client.force_login(faculty_user)
    for name in ("whatsapp_settings", "whatsapp_log", "whatsapp_tomorrow", "whatsapp_centre"):
        assert client.get(reverse(name)).status_code in (302, 403)
