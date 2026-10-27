from django.db import migrations


def copy(apps, schema_editor):
    """Category caps become category rules (maximum); every period gets its workload policy."""
    Cap = apps.get_model("exams", "DesignationCap")
    Period = apps.get_model("exams", "ExamPeriod")
    CategoryRule = apps.get_model("workload", "CategoryRule")
    Policy = apps.get_model("workload", "WorkloadPolicy")
    for cap in Cap.objects.all():
        CategoryRule.objects.update_or_create(
            period_id=cap.period_id, category_id=cap.category_id, defaults={"max_duties": cap.max_duties}
        )
    for period in Period.objects.all():
        Policy.objects.get_or_create(period=period)


class Migration(migrations.Migration):

    dependencies = [
        ("workload", "0001_initial"),
        ("exams", "0004_room_inventory"),
    ]

    operations = [migrations.RunPython(copy, migrations.RunPython.noop)]
