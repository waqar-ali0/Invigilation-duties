"""Optional Google Sheets source for imports.

Access uses a Google *service account* with the read-only Sheets scope: share the sheet with the
service account's email (Viewer) and put the path of its JSON key in .env as
GOOGLE_SERVICE_ACCOUNT_FILE. A sync never writes to the database directly: it makes an import
batch that goes through the same column choice, preview and confirmation as an Excel upload."""

import hashlib
import json
import re
import urllib.parse
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import BatchStatus, ImportBatch, SheetSource
from .reader import MAX_ROWS, _plain

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
API = "https://sheets.googleapis.com/v4/spreadsheets"


class SheetError(Exception):
    pass


def configured():
    path = getattr(settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "")
    return bool(path)


def sheet_id(text):
    """The spreadsheet ID from a link or a bare ID."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", text or "")
    return m.group(1) if m else (text or "").strip()


def _token():
    path = getattr(settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "")
    if not path:
        raise SheetError("Google Sheets is not set up: GOOGLE_SERVICE_ACCOUNT_FILE is missing from .env.")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - dependency missing on the server
        raise SheetError("The google-auth package is not installed.") from exc
    try:
        creds = service_account.Credentials.from_service_account_file(path, scopes=[SCOPE])
        creds.refresh(Request())
    except Exception as exc:
        raise SheetError(f"Google sign-in failed: {exc}") from None
    return creds.token


def fetch_values(source):
    """[[cell, …], …] of the source's worksheet (formatted values as people see them)."""
    import requests

    rng = source.worksheet.strip() or "A1:ZZ"
    if source.worksheet.strip():
        rng = f"'{source.worksheet.strip()}'"
    url = f"{API}/{urllib.parse.quote(sheet_id(source.spreadsheet_id))}/values/{urllib.parse.quote(rng)}"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {_token()}"}, params={"majorDimension": "ROWS"}, timeout=30)
    except requests.RequestException as exc:
        raise SheetError(f"Could not reach Google: {exc}") from None
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        hint = " Share the sheet with the service account's email as Viewer." if resp.status_code in (403, 404) else ""
        raise SheetError(f"Google refused ({resp.status_code}): {message or 'error'}.{hint}")
    return resp.json().get("values", [])


def to_table(values):
    """(headers, rows) in the same shape as the file reader."""
    raw = [list(r) for r in values]
    skipped = 0
    while raw and all(_plain(v) is None for v in raw[0]):
        raw.pop(0)
        skipped += 1
    if not raw:
        raise SheetError("The sheet is empty.")
    width = max(len(r) for r in raw)
    headers, seen = [], {}
    for i in range(width):
        value = _plain(raw[0][i]) if i < len(raw[0]) else None
        label = str(value) if value is not None else f"Column {i + 1}"
        seen[label] = seen.get(label, 0) + 1
        headers.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    rows = []
    for number, values_row in enumerate(raw[1:], start=2 + skipped):
        cells = [_plain(values_row[i]) if i < len(values_row) else None for i in range(width)]
        if any(v is not None for v in cells):
            rows.append([number, cells])
    if len(rows) > MAX_ROWS:
        raise SheetError(f"The sheet has more than {MAX_ROWS} rows.")
    return headers, rows


def sync(source, user=None, only_if_changed=False):
    """Fetch the sheet and make an import batch for review. Returns the batch, or None when
    `only_if_changed` and nothing changed since the last check."""
    from .views import KINDS

    source.last_checked_at = timezone.now()
    try:
        headers, rows = to_table(fetch_values(source))
    except SheetError as e:
        source.last_error = str(e)[:500]
        source.save(update_fields=["last_checked_at", "last_error"])
        raise
    digest = hashlib.sha256(json.dumps([headers, rows], default=str).encode("utf-8")).hexdigest()
    if only_if_changed and digest == source.last_hash:
        source.last_error = ""
        source.save(update_fields=["last_checked_at", "last_error"])
        return None
    kind = KINDS[source.kind]
    previous = source.batches.filter(status=BatchStatus.DONE).order_by("-created_at").first()
    same_layout = previous is not None and previous.headers == headers
    batch = ImportBatch.objects.create(
        kind=source.kind,
        filename=f"Google Sheet: {source.name}",
        uploaded_by=user,
        headers=headers,
        rows=rows,
        mapping=previous.mapping if same_layout else kind.guess_mapping(headers),
        options=previous.options if same_layout else kind.default_options(),
        source=source,
        status=BatchStatus.PREVIEW if same_layout else BatchStatus.MAPPING,
    )
    source.last_hash, source.last_error = digest, ""
    source.save(update_fields=["last_checked_at", "last_error", "last_hash"])
    return batch


def due_sources(now=None):
    now = now or timezone.now()
    for s in SheetSource.objects.filter(active=True, sync_hours__gt=0):
        if s.last_checked_at is None or s.last_checked_at <= now - timedelta(hours=s.sync_hours):
            yield s
