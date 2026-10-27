import os
import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from ops import backups


def test_healthz_needs_no_login(client, db):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_healthz_reports_database_failure(client, db):
    with mock.patch("ops.views.connection") as conn:
        conn.cursor.side_effect = Exception("down")
        response = client.get("/healthz/")
    assert response.status_code == 503


def test_serve_passes_settings_to_waitress(settings):
    with mock.patch("waitress.serve") as serve:
        call_command("serve", "--host", "127.0.0.1", "--port", "8123", "--threads", "6")
    kwargs = serve.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8123
    assert kwargs["threads"] == 6
    assert kwargs["trusted_proxy"] == "127.0.0.1"
    assert "x-forwarded-proto" in kwargs["trusted_proxy_headers"]


def _sqlite_db(path):
    con = sqlite3.connect(path)
    con.execute("create table t (x int)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()


def test_sqlite_backup_and_restore(tmp_path):
    source = tmp_path / "db.sqlite3"
    _sqlite_db(source)
    config = {"ENGINE": "django.db.backends.sqlite3", "NAME": str(source)}

    made = backups.backup(config, tmp_path / "backups", keep_days=30)
    assert made.exists() and made.suffix == ".sqlite3"

    sqlite3.connect(source).execute("drop table t").connection.commit()
    backups.restore(config, made)
    assert sqlite3.connect(source).execute("select x from t").fetchone() == (42,)


def test_old_backups_are_pruned(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    old = folder / "invigilation-20200101-020000.sqlite3"
    recent = folder / "invigilation-20990101-020000.sqlite3"
    unrelated = folder / "notes.txt"
    for f in (old, recent, unrelated):
        f.write_text("x")
    forty_days_ago = time.time() - 40 * 86400
    os.utime(old, (forty_days_ago, forty_days_ago))
    os.utime(unrelated, (forty_days_ago, forty_days_ago))

    removed = backups.prune(folder, keep_days=30)

    assert removed == [old]
    assert recent.exists() and unrelated.exists()


def test_postgres_backup_runs_pg_dump(tmp_path):
    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "invigilation",
        "USER": "duties",
        "PASSWORD": "s3cret",
        "HOST": "localhost",
        "PORT": 5432,
    }
    with mock.patch("ops.backups.subprocess.run") as run:
        made = backups.backup(config, tmp_path, keep_days=30, pg_bin="C:/pg/bin")
    args, kwargs = run.call_args
    command = args[0]
    assert Path(command[0]) == Path("C:/pg/bin/pg_dump")
    assert "--format=custom" in command and f"--file={made}" in command
    assert command[-1] == "invigilation"
    assert kwargs["env"]["PGPASSWORD"] == "s3cret"
    assert "s3cret" not in " ".join(command)
    assert kwargs["check"] is True


def test_postgres_restore_runs_pg_restore(tmp_path):
    dump = tmp_path / "invigilation-20261024-020000.dump"
    dump.write_bytes(b"x")
    config = {"ENGINE": "django.db.backends.postgresql", "NAME": "invigilation", "USER": "duties", "PASSWORD": "pw", "HOST": "", "PORT": ""}
    with mock.patch("ops.backups.subprocess.run") as run:
        backups.restore(config, dump)
    command = run.call_args.args[0]
    assert Path(command[0]).name == "pg_restore"
    assert "--clean" in command and "--if-exists" in command
    assert command[-1] == str(dump)


def test_restore_command_requires_confirmation(tmp_path, db):
    dump = tmp_path / "x.sqlite3"
    dump.write_bytes(b"")
    with pytest.raises(CommandError, match="--yes"):
        call_command("restore_db", str(dump))


def test_restore_command_rejects_missing_file(tmp_path, db):
    with pytest.raises(CommandError, match="not found"):
        call_command("restore_db", str(tmp_path / "missing.dump"), "--yes")


def test_backup_command_writes_to_folder(tmp_path, settings):
    with mock.patch("ops.backups.backup", return_value=tmp_path / "b.sqlite3") as backup:
        call_command("backup_db", "--dir", str(tmp_path), "--keep-days", "7")
    assert backup.call_args.kwargs["keep_days"] == 7
    assert backup.call_args.args[1] == tmp_path
