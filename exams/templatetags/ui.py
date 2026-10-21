"""Small presentation helpers shared by the templates."""

from django import template

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
