from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from notifications.triggers import queue_reminders


class Command(BaseCommand):
    help = "Queue reminder emails for tomorrow's duties. Schedule this daily in the evening (e.g. 18:00)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Day of the duties to remind about (YYYY-MM-DD). Default: tomorrow.")

    def handle(self, *args, **options):
        if options["date"]:
            try:
                day = date.fromisoformat(options["date"])
            except ValueError as exc:
                raise CommandError("Use --date YYYY-MM-DD.") from exc
        else:
            day = timezone.localdate() + timedelta(days=1)
        count = queue_reminders(day)
        self.stdout.write(f"Queued {count} reminder(s) for {day.isoformat()}.")
