import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from ops import backups


class Command(BaseCommand):
    help = "Back up the database into BACKUP_DIR and delete backups older than BACKUP_KEEP_DAYS (default 30)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=os.environ.get("BACKUP_DIR", str(settings.BASE_DIR / "backups")))
        parser.add_argument("--keep-days", type=int, default=int(os.environ.get("BACKUP_KEEP_DAYS", "30")))

    def handle(self, dir, keep_days, **options):
        made = backups.backup(settings.DATABASES["default"], Path(dir), keep_days=keep_days, pg_bin=os.environ.get("PG_BIN"))
        self.stdout.write(self.style.SUCCESS(f"Backup written to {made}"))
