import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ops import backups


class Command(BaseCommand):
    help = "Replace the current database with a backup file. Stop the web service first."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Backup file (.dump for PostgreSQL, .sqlite3 for SQLite)")
        parser.add_argument("--yes", action="store_true", help="Confirm that the current data will be overwritten")

    def handle(self, file, yes, **options):
        path = Path(file)
        if not path.is_file():
            raise CommandError(f"Backup file not found: {path}")
        if not yes:
            raise CommandError("This overwrites all current data. Run again with --yes to confirm.")
        from django.db import connections

        connections.close_all()
        backups.restore(settings.DATABASES["default"], path, pg_bin=os.environ.get("PG_BIN"))
        self.stdout.write(self.style.SUCCESS(f"Restored from {path}"))
