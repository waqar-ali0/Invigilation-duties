from .models import AuditLog


def log(actor, action, obj, before=None, after=None):
    """Record `action` on `obj` by `actor` (None for the system)."""
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=obj._meta.label,
        object_id=str(obj.pk),
        before=before,
        after=after,
    )
