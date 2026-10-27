"""Database backup and restore for SQLite and PostgreSQL.

PostgreSQL uses pg_dump/pg_restore (custom format); SQLite uses its online backup API,
so both are safe to run while the site is up. Backups older than `keep_days` are deleted.
"""

import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

PREFIX = "invigilation-"


def is_postgres(config):
    return "postgresql" in config["ENGINE"]


def _tool(name, pg_bin):
    return str(Path(pg_bin) / name) if pg_bin else name


def _pg_env(config):
    env = os.environ.copy()
    if config.get("PASSWORD"):
        env["PGPASSWORD"] = str(config["PASSWORD"])
    return env


def _pg_connection_args(config):
    args = []
    if config.get("HOST"):
        args.append(f"--host={config['HOST']}")
    if config.get("PORT"):
        args.append(f"--port={config['PORT']}")
    if config.get("USER"):
        args.append(f"--username={config['USER']}")
    return args


def backup(config, folder, keep_days=30, pg_bin=None):
    """Write a timestamped backup into `folder`, prune old ones and return the new file's path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if is_postgres(config):
        target = folder / f"{PREFIX}{stamp}.dump"
        command = [_tool("pg_dump", pg_bin), "--format=custom", "--no-owner", f"--file={target}", *_pg_connection_args(config), config["NAME"]]
        subprocess.run(command, env=_pg_env(config), check=True)
    else:
        target = folder / f"{PREFIX}{stamp}.sqlite3"
        source = sqlite3.connect(config["NAME"])
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
    prune(folder, keep_days)
    return target


def prune(folder, keep_days):
    """Delete this system's backups older than `keep_days`; return the deleted paths."""
    cutoff = time.time() - keep_days * 86400
    removed = []
    for f in sorted(Path(folder).glob(f"{PREFIX}*")):
        if f.suffix in (".dump", ".sqlite3") and f.stat().st_mtime < cutoff:
            f.unlink()
            removed.append(f)
    return removed


def restore(config, path, pg_bin=None):
    """Replace the current database with the contents of backup file `path`."""
    path = Path(path)
    if is_postgres(config):
        command = [_tool("pg_restore", pg_bin), "--clean", "--if-exists", "--no-owner", "--single-transaction",
                   f"--dbname={config['NAME']}", *_pg_connection_args(config), str(path)]
        subprocess.run(command, env=_pg_env(config), check=True)
    else:
        source = sqlite3.connect(path)
        dest = sqlite3.connect(config["NAME"])
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
