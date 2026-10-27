from django.core.management.base import BaseCommand

from messaging.models import WhatsAppSettings
from messaging.services import due_for_dispatch, queue_reminders, send_pending


class Command(BaseCommand):
    help = (
        "Run every few minutes (Task Scheduler): after the daily dispatch time, queue reminders for "
        "the coming duties (never twice), then submit whatever is due. Does nothing when WhatsApp "
        "is not set up or reminders are switched off."
    )

    def handle(self, *args, **options):
        cfg = WhatsAppSettings.get()
        if not cfg.enabled:
            self.stdout.write("WhatsApp reminders are switched off.")
            return
        day = due_for_dispatch()
        if day is not None:
            queued, skipped = queue_reminders(day)
            if queued or skipped:
                self.stdout.write(f"Reminders for {day}: {queued} queued, {len(skipped)} not possible.")
        submitted, failed, note = send_pending()
        self.stdout.write(note or f"Submitted {submitted}, failed {failed}.")
