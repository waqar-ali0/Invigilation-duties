from django.db import migrations


def seed(apps, schema_editor):
    ExamType = apps.get_model("exams", "ExamType")
    for order, name in enumerate(["Midterm", "Final", "Supplementary", "Special"]):
        ExamType.objects.get_or_create(name=name, defaults={"order": order})


class Migration(migrations.Migration):
    dependencies = [("exams", "0007_period_lifecycle_and_details")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]