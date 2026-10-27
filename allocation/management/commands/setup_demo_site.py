"""Build a ready-to-show demo site on an empty database (used by the hosted demo on start-up).

Creates the demo data (20 departments, 200 faculty, a published "Demo Finals" and a draft
"Demo Midterms"), and the demo logins below, all with the password in DEMO_PASSWORD. Safe to run
again: whatever already exists is left alone."""

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User
from exams.models import ExamPeriod, PeriodStatus
from faculty.models import Department, Faculty

LOGINS = [
    ("demo.admin@uni.edu", Role.ADMIN, None),
    ("demo.staff@uni.edu", Role.EXAM_STAFF, None),
    ("demo.hod@uni.edu", Role.HOD, "DEMO000"),
    ("demo.faculty@uni.edu", Role.FACULTY, "DEMO001"),
]


class Command(BaseCommand):
    help = "Create the demo data and demo logins (password from DEMO_PASSWORD) if they are missing."

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD", "")
        if len(password) < 10:
            raise CommandError("Set DEMO_PASSWORD (at least 10 characters) for the demo logins.")
        if not ExamPeriod.objects.filter(name="Demo Finals").exists():
            call_command("load_demo", verbosity=0)
        if not ExamPeriod.objects.filter(name="Demo Midterms").exists():
            call_command("load_demo", name="Demo Midterms", start="2027-01-11", courses=6, verbosity=0)
        with transaction.atomic():
            for email, role, employee_id in LOGINS:
                user = User.objects.filter(email=email).first()
                if user is None:
                    user = User.objects.create_user(email, password, role=role)
                if employee_id:
                    faculty = Faculty.objects.get(employee_id=employee_id)
                    Faculty.objects.filter(pk=faculty.pk).update(user=user, email=email)
                    if role == Role.HOD:
                        Department.objects.filter(pk=faculty.department_id).update(hod=faculty)
            admin = User.objects.get(email="demo.admin@uni.edu")
        finals = ExamPeriod.objects.get(name="Demo Finals")
        if finals.status != PeriodStatus.PUBLISHED:
            from allocation.services import publish_period, run_allocation

            run_allocation(finals, admin, seed=1)
            publish_period(finals, admin, acknowledge_unfilled=True, confirm_rules=True)
        self.stdout.write(self.style.SUCCESS("Demo site ready: " + ", ".join(e for e, _, _ in LOGINS)))
