"""Every {% icon %} used in a template exists in the sprite and is allowed by the tag."""

import re
from pathlib import Path

from exams.templatetags.ui import ICONS

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_every_used_icon_exists():
    sprite = set(re.findall(r'id="i-([a-z0-9-]+)"', (TEMPLATES / "_icons.html").read_text(encoding="utf-8")))
    used = set()
    for path in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r'\{% icon "([a-z0-9-]+)" %\}', path.read_text(encoding="utf-8")))
    assert used - ICONS == set()
    assert used - sprite == set()
