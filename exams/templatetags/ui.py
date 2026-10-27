"""Small presentation helpers shared by the templates."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

TONES = {
    "ok": {"published", "approved", "completed", "sent"},
    "warn": {"pending", "pending_peer", "pending_admin", "sending"},
    "info": {"availability_open", "allocating", "queued", "assigned"},
    "danger": {"rejected", "declined", "absent", "failed"},
}


@register.filter
def tone(status):
    """Colour family for a status badge: ok, warn, info, danger or neutral."""
    for name, statuses in TONES.items():
        if status in statuses:
            return name
    return "neutral"


# Names of the symbols in templates/_icons.html.
ICONS = {
    "calendar", "inbox", "repeat", "check-square", "users", "mail", "shield", "settings", "clipboard",
    "calendar-x", "building", "search", "logout", "download", "upload", "play", "trash", "send", "plus",
    "check", "x", "menu", "file", "chart", "clock", "alert", "arrow-left", "arrow-right", "user-plus", "eye",
    "bell",
}


@register.simple_tag
def icon(name):
    """An inline icon from the sprite in base.html: {% icon "calendar" %}."""
    if name not in ICONS:
        return ""
    return mark_safe(f'<svg class="icon" aria-hidden="true"><use href="#i-{name}"></use></svg>')


@register.filter
def initials(email):
    """Avatar letters from an email: ali.raza@uni.edu -> AR."""
    local = (email or "").split("@")[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    if not parts:
        return "?"
    letters = "".join(p[0] for p in parts[:2] if p[0].isalpha()) or parts[0][0]
    return letters.upper()
