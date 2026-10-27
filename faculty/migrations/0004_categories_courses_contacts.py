import django.db.models.deletion
from django.db import migrations, models

SEED = [
    ("professor", "Professor", False, False),
    ("associate_professor", "Associate Professor", False, False),
    ("assistant_professor", "Assistant Professor", False, False),
    ("lecturer", "Lecturer", False, False),
    ("lab_engineer", "Lab Engineer", False, False),
    ("visiting", "Visiting Faculty", True, False),
    ("backup", "Backup Faculty", False, True),
]


def seed_and_link(apps, schema_editor):
    Category = apps.get_model("faculty", "FacultyCategory")
    Faculty = apps.get_model("faculty", "Faculty")
    by_code = {}
    for order, (code, name, visiting, backup) in enumerate(SEED):
        by_code[code], _ = Category.objects.get_or_create(
            code=code, defaults={"name": name, "is_visiting": visiting, "is_backup": backup, "order": order}
        )
    for f in Faculty.objects.all():
        f.category = by_code.get(f.designation) or Category.objects.get_or_create(
            code=f.designation, defaults={"name": f.designation.replace("_", " ").title(), "order": 99}
        )[0]
        if f.designation == "visiting":
            f.employment_status = "visiting"
        f.save(update_fields=["category", "employment_status"])


class Migration(migrations.Migration):

    dependencies = [
        ("faculty", "0003_email_ci_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="FacultyCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(help_text="Short fixed identifier; not shown to users.", max_length=40, unique=True)),
                ("name", models.CharField(max_length=80, unique=True)),
                ("is_active", models.BooleanField(default=True, help_text="Inactive categories can't be given to new faculty.")),
                ("is_visiting", models.BooleanField(default=False, help_text="Duties can be set per course taught.")),
                ("is_backup", models.BooleanField(default=False, help_text="Members join the backup pool by default.")),
                ("order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["order", "name"], "verbose_name_plural": "faculty categories"},
        ),
        migrations.CreateModel(
            name="Counter",
            fields=[
                ("name", models.CharField(max_length=40, primary_key=True, serialize=False)),
                ("value", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="Course",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=30, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="courses", to="faculty.department")),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.AddField(
            model_name="faculty",
            name="category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="faculty", to="faculty.facultycategory"),
        ),
        migrations.AddField(
            model_name="faculty",
            name="employment_status",
            field=models.CharField(choices=[("permanent", "Permanent"), ("contract", "Contract"), ("visiting", "Visiting"), ("on_leave", "On leave"), ("left", "Left the university")], default="permanent", max_length=12),
        ),
        migrations.RunPython(seed_and_link, migrations.RunPython.noop),
        migrations.RemoveField(model_name="faculty", name="designation"),
        migrations.AlterField(
            model_name="faculty",
            name="category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="faculty", to="faculty.facultycategory"),
        ),
        migrations.AddField(
            model_name="faculty",
            name="courses",
            field=models.ManyToManyField(blank=True, related_name="teachers", to="faculty.course"),
        ),
        migrations.AddField(
            model_name="faculty",
            name="phone",
            field=models.CharField(blank=True, help_text="As entered, e.g. 0300-1234567.", max_length=40),
        ),
        migrations.AddField(
            model_name="faculty",
            name="phone_e164",
            field=models.CharField(blank=True, db_index=True, editable=False, help_text="Worked out from the phone number, e.g. +923001234567. Empty when the number is not valid.", max_length=16, verbose_name="phone (international)"),
        ),
        migrations.AddField(model_name="faculty", name="remarks", field=models.TextField(blank=True)),
        migrations.AddField(model_name="faculty", name="created_at", field=models.DateTimeField(auto_now_add=True, null=True)),
        migrations.AddField(model_name="faculty", name="updated_at", field=models.DateTimeField(auto_now=True, null=True)),
        migrations.AlterField(
            model_name="faculty",
            name="email",
            field=models.EmailField(blank=True, help_text="Needed only for an account and emails.", max_length=254, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="faculty",
            name="employee_id",
            field=models.CharField(blank=True, help_text="Leave empty to generate one (FAC-0001). Never changes afterwards.", max_length=50, unique=True, verbose_name="faculty ID"),
        ),
    ]
