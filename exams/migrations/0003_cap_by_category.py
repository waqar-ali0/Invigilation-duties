import django.db.models.deletion
from django.db import migrations, models


def link_categories(apps, schema_editor):
    Cap = apps.get_model("exams", "DesignationCap")
    Category = apps.get_model("faculty", "FacultyCategory")
    for cap in Cap.objects.all():
        cap.category = Category.objects.get_or_create(
            code=cap.designation, defaults={"name": cap.designation.replace("_", " ").title(), "order": 99}
        )[0]
        cap.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0002_periodsettings_swap_cutoff_hours"),
        ("faculty", "0004_categories_courses_contacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="designationcap",
            name="category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="faculty.facultycategory"),
        ),
        migrations.RunPython(link_categories, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="designationcap", name="unique_designation_cap"),
        migrations.RemoveField(model_name="designationcap", name="designation"),
        migrations.AlterField(
            model_name="designationcap",
            name="category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="faculty.facultycategory"),
        ),
        migrations.AddConstraint(
            model_name="designationcap",
            constraint=models.UniqueConstraint(fields=("period", "category"), name="unique_category_cap"),
        ),
        migrations.AlterModelOptions(name="designationcap", options={"verbose_name": "category cap"}),
    ]
