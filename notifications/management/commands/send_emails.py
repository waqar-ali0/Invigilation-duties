from django.core.management.base import BaseCommand

from notifications.outbox import send_queued


class Command(BaseCommand):
    help = "Send queued emails. Schedule this every few minutes."

    def handle(self, *args, **options):
        sent, failed = send_queued()
        self.stdout.write(f"Sent {sent}, failed {failed}.")
