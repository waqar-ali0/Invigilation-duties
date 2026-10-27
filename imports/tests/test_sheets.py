from unittest import mock

import pytest
from django.core.management import call_command
from django.urls import reverse

from faculty.models import Faculty
from imports import sheets
from imports.models import BatchStatus, ImportBatch, SheetSource
from notifications.models import Notification

pytestmark = pytest.mark.django_db
VALUES = [["Teacher Name", "Rank", "Mobile", "Dept"], ["Ali Raza", "Lecturer", "0300-1234567", "Computer Science"]]


@pytest.fixture
def source(db):
    return SheetSource.objects.create(name="Staff list", kind="faculty", spreadsheet_id="https://docs.google.com/spreadsheets/d/AbC-123_x/edit#gid=0", sync_hours=1)


def test_sheet_id_from_link():
    assert sheets.sheet_id("https://docs.google.com/spreadsheets/d/AbC-123_x/edit#gid=0") == "AbC-123_x"
    assert sheets.sheet_id(" AbC-123_x ") == "AbC-123_x"


def test_sync_makes_a_batch_that_still_needs_confirmation(admin_client, admin_user, source, dept, settings):
    settings.GOOGLE_SERVICE_ACCOUNT_FILE = "key.json"
    with mock.patch.object(sheets, "fetch_values", return_value=VALUES):
        r = admin_client.post(reverse("import_sheets"), {"action": "sync", "id": source.pk})
    batch = ImportBatch.objects.get()
    assert r.url == reverse("import_batch", args=[batch.pk])
    assert batch.source == source and batch.status == BatchStatus.MAPPING and not Faculty.objects.exists()
    assert {batch.headers[i] for i in batch.mapping.values()} == {"Teacher Name", "Rank", "Mobile", "Dept"}


def test_unchanged_sheet_is_skipped_and_layout_reused(admin_user, source, dept):
    with mock.patch.object(sheets, "fetch_values", return_value=VALUES):
        first = sheets.sync(source, admin_user)
        ImportBatch.objects.filter(pk=first.pk).update(status=BatchStatus.DONE, options={"default_department": str(dept.pk)})
        assert sheets.sync(source, only_if_changed=True) is None
    changed = VALUES + [["Sara Khan", "Lecturer", "0300-7654321", "Computer Science"]]
    with mock.patch.object(sheets, "fetch_values", return_value=changed):
        second = sheets.sync(source, only_if_changed=True)
    assert second.status == BatchStatus.PREVIEW and second.mapping == first.mapping
    assert second.options == {"default_department": str(dept.pk)}


def test_errors_are_recorded_and_shown(admin_client, source, settings):
    settings.GOOGLE_SERVICE_ACCOUNT_FILE = "key.json"
    with mock.patch.object(sheets, "fetch_values", side_effect=sheets.SheetError("Google refused (403): no access.")):
        admin_client.post(reverse("import_sheets"), {"action": "sync", "id": source.pk})
    source.refresh_from_db()
    assert "403" in source.last_error and not ImportBatch.objects.exists()
    assert "403" in admin_client.get(reverse("import_sheets")).content.decode()


def test_scheduled_check_notifies_the_exam_office(admin_user, source, settings):
    settings.GOOGLE_SERVICE_ACCOUNT_FILE = "key.json"
    with mock.patch.object(sheets, "fetch_values", return_value=VALUES):
        call_command("sync_sheets")
    note = Notification.objects.get(user=admin_user)
    assert "Staff list" in note.title and note.link == reverse("import_batch", args=[ImportBatch.objects.get().pk])


def test_not_set_up_is_explained(admin_client, settings):
    settings.GOOGLE_SERVICE_ACCOUNT_FILE = ""
    assert "Not set up" in admin_client.get(reverse("import_sheets")).content.decode()
    with pytest.raises(sheets.SheetError, match="not set up"):
        sheets._token()


def test_empty_sheet_is_refused():
    with pytest.raises(sheets.SheetError, match="empty"):
        sheets.to_table([[], []])
