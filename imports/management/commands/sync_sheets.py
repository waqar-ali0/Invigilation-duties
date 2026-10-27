from django.core.management.base import BaseCommand
from django.urls import reverse

from accounts.models import Role, User
from imports.sheets import SheetError, configured, due_sources, sync
from notifications.models import EmailKind, Notification


class Command(BaseCommand):
    help = (
        "Check the Google Sheets set to sync automatically. When one has changed, prepare an import "
        "for review and notify the exam office. Nothing is saved without confirmation."
    )

    def handle(self, *args, **options):
        if not configured():
            self.stdout.write("Google Sheets is not set up (GOOGLE_SERVICE_ACCOUNT_FILE).")
            return
        office = list(User.objects.filter(role=Role.ADMIN, is_active=True))
        for source in due_sources():
            try:
                batch = sync(source, only_if_changed=True)
            except SheetError as e:
                self.stdout.write(f"{source.name}: {e}")
                continue
            if batch is None:
                self.stdout.write(f"{source.name}: no changes.")
                continue
            for user in office:
                Notification.objects.create(
                    user=user, kind=EmailKind.ANNOUNCEMENT, title=f"Google Sheet “{source.name}” changed",
                    body=f"{len(batch.rows)} rows are ready to review. Nothing is saved until you confirm.",
                    link=reverse("import_batch", args=[batch.pk]),
                )
            self.stdout.write(f"{source.name}: changes prepared for review (import {batch.pk}).")
