from django.db import migrations

DEFAULTS = {
    "reminder": ("duty_reminder", ["name", "date", "shift", "room", "building", "reporting_time"]),
    "new_duty": ("duty_assigned", ["name", "date", "shift", "room", "building"]),
    "changed": ("duty_changed", ["name", "date", "shift", "room", "building"]),
    "room_changed": ("duty_room_changed", ["name", "date", "shift", "room", "building"]),
    "replacement": ("replacement_duty", ["name", "date", "shift", "room", "building"]),
    "cancelled": ("duty_cancelled", ["name", "date", "shift", "room"]),
    "announcement": ("exam_announcement", ["name", "message"]),
}


def create(apps, schema_editor):
    """Suggested template names, inactive until the office enters the names Meta approved."""
    Template = apps.get_model("messaging", "MessageTemplate")
    for kind, (name, params) in DEFAULTS.items():
        Template.objects.get_or_create(kind=kind, defaults={"name": name, "language": "en", "parameters": params, "active": False})
    apps.get_model("messaging", "WhatsAppSettings").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("messaging", "0001_initial")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
