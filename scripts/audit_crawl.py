r"""Open every page each role can reach (GET only) and report errors, broken links, 403s and slow pages.

Runs against your local database (db.sqlite3) and only reads pages, so nothing is changed.
Pass the login emails to check, one per role; by default it uses the demo accounts:

    .venv\Scripts\python scripts\audit_crawl.py
    .venv\Scripts\python scripts\audit_crawl.py office@uni.edu hod@uni.edu teacher@uni.edu staff@uni.edu

Exits with status 1 if any problem was found.
"""

import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
settings.ALLOWED_HOSTS = ["testserver"]
settings.DEBUG = True  # so query counts are recorded

from accounts.models import User  # noqa: E402

ROLES = sys.argv[1:] or ["demo.admin@uni.edu", "demo.hod@uni.edu", "demo.faculty@uni.edu", "demo.staff@uni.edu", "demo010@uni.edu"]
SKIP = ("/logout/", "/admin/logout/", "/static/", "/password-reset/", "/activate/")
PER_PATTERN = 4  # pages sampled per URL shape (e.g. /periods/<n>/rooms/<n>/)
SLOW = 1.5
problems = []


def shape(path):
    return re.sub(r"\d+", "<n>", path.split("?")[0])


# Warm-up: the first request after start-up loads the app and compiles templates; don't time it.
_warm = Client()
_warm.get("/login/")
for _user in User.objects.filter(email__in=ROLES)[:1]:
    _warm.force_login(_user)
    _warm.get("/", follow=True)

for email in ROLES:
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        problems.append((email, "-", "no such account"))
        continue
    c = Client()
    c.force_login(user)
    seen, queue, per_shape = set(), ["/"], defaultdict(int)
    pages = slow = 0
    while queue and pages < 400:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        s = shape(url)
        if per_shape[s] >= PER_PATTERN:
            continue
        per_shape[s] += 1
        reset_queries()
        t0 = time.perf_counter()
        r = c.get(url, follow=False)
        dt = time.perf_counter() - t0
        pages += 1
        q = len(connection.queries)
        if r.status_code >= 500 or r.status_code == 404:
            problems.append((email, url, f"HTTP {r.status_code}"))
            continue
        if r.status_code == 403:
            problems.append((email, url, "403 on a link shown to this role"))
            continue
        if dt > SLOW:
            slow += 1
            problems.append((email, url, f"slow {dt:.2f}s, {q} queries"))
        elif q > 60:
            problems.append((email, url, f"{q} queries"))
        if r.status_code in (301, 302):
            loc = r["Location"]
            if loc.startswith("/") and not loc.startswith(SKIP):
                queue.append(loc)
            continue
        ctype = r.get("Content-Type", "")
        if "text/html" not in ctype:
            continue
        body = r.content.decode("utf-8", "replace")
        for bad in ("TemplateSyntaxError", "VariableDoesNotExist", "Traceback", "None —", "{{", "{%"):
            if bad in body.split("<main>")[-1]:
                problems.append((email, url, f"page contains {bad!r}"))
        for href in re.findall(r'href="([^"#]+)"', body):
            target = urljoin(url, href)
            parts = urlsplit(target)
            if parts.netloc or target.startswith(SKIP) or not target.startswith("/"):
                continue
            if "/admin/" in target and target.rstrip("/").count("/") > 3 and not target.endswith("/change/"):
                continue  # Django admin internals: sample the index, lists and change pages only
            if target.endswith(("/delete/", "/history/")):
                continue
            queue.append(parts.path + (f"?{parts.query}" if parts.query else ""))
    print(f"{email:24} {pages:3} pages opened, {len(per_shape):3} page types")

print()
if problems:
    print(f"{len(problems)} problem(s):")
    for email, url, what in problems:
        print(f"  {email:24} {url}  -> {what}")
else:
    print("No problems: every reachable page opened without errors, broken links, 403s or slow responses.")
sys.exit(1 if problems else 0)
