import random
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import (
    Building,
    ExamPeriod,
    ExamSession,
    Room,
    RoomAllocation,
    TimeSlot,
    required_invigilators,
)
from faculty.models import Department, Designation, Faculty, FacultyCategory
from workload.models import CategoryRule

PERIOD_NAME = "Demo Finals"
FIRST = ["Ayesha", "Bilal", "Fatima", "Hamza", "Hina", "Imran", "Kamran", "Maryam", "Nadia", "Omar",
         "Rabia", "Saad", "Sana", "Tariq", "Usman", "Zainab", "Ali", "Farah", "Junaid", "Mehwish"]
LAST = ["Ahmed", "Khan", "Malik", "Hussain", "Qureshi", "Shah", "Butt", "Siddiqui", "Raza", "Iqbal"]
# Category mix for each department's ten faculty members.
MIX = [Designation.PROFESSOR] + [Designation.ASSOCIATE_PROFESSOR] * 2 + [Designation.ASSISTANT_PROFESSOR] * 3 + [
    Designation.LECTURER
] * 3 + [Designation.VISITING]
SLOT_TIMES = [(time(9), time(12)), (time(14), time(17))]


class Command(BaseCommand):
    help = (
        "Create demo data: 20 departments, 200 faculty (once) and a 10-day exam period "
        "(default 'Demo Finals' from 2026-12-01). Run again with --name/--start for more periods."
    )

    def add_arguments(self, parser):
        parser.add_argument("--name", default=PERIOD_NAME, help="Exam period name.")
        parser.add_argument("--start", default="2026-12-01", help="First exam day (YYYY-MM-DD).")
        parser.add_argument(
            "--courses", type=int, default=15, help="Courses per time slot (2 rooms each); lower = lighter load."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        name = options["name"]
        if ExamPeriod.objects.filter(name=name).exists():
            raise CommandError(f"{name} already exists.")
        try:
            start = date.fromisoformat(options["start"])
        except ValueError as exc:
            raise CommandError("Use --start YYYY-MM-DD.") from exc
        courses = options["courses"]
        rng = random.Random(2026)

        departments = []
        for i in range(1, 21):
            dept, _ = Department.objects.get_or_create(name=f"Department {i:02d}")
            departments.append(dept)

        faculty_by_dept = {}
        exempt = set(rng.sample(range(200), 5))
        n = 0
        existing = Faculty.objects.filter(employee_id__startswith="DEMO")
        if existing.exists():  # a later demo period reuses the demo faculty
            for f in existing:
                faculty_by_dept.setdefault(f.department_id, []).append(f)
            n = existing.count()
        for dept in departments if not faculty_by_dept else []:
            people = []
            for designation in MIX:
                people.append(
                    Faculty(
                        employee_id=f"DEMO{n:03d}",
                        name=f"{rng.choice(FIRST)} {rng.choice(LAST)} {n:03d}",
                        email=f"demo{n:03d}@uni.edu",
                        department=dept,
                        category=FacultyCategory.get(designation),
                        phone=f"0300{n:07d}",
                        phone_e164=f"+92300{n:07d}",
                        is_exempt=n in exempt,
                    )
                )
                n += 1
            faculty_by_dept[dept.id] = Faculty.objects.bulk_create(people)

        period = ExamPeriod.objects.create(name=name, start_date=start, end_date=start + timedelta(days=9))
        CategoryRule.objects.create(period=period, category=FacultyCategory.get(Designation.PROFESSOR), max_duties=4)
        period.settings.max_duties_per_faculty = 8
        period.settings.save(update_fields=["max_duties_per_faculty"])
        building, _ = Building.objects.get_or_create(name="Demo Block", defaults={"campus": "Main campus"})
        inventory = {}

        rooms = []
        for day in range(10):
            for slot_no, (t1, t2) in enumerate(SLOT_TIMES):
                slot = TimeSlot.objects.create(period=period, date=start + timedelta(days=day), start_time=t1, end_time=t2)
                for c in range(courses):
                    dept = departments[(day * 2 + slot_no + c) % 20]
                    session = ExamSession.objects.create(
                        slot=slot,
                        course_code=f"D{dept.name[-2:]}-{day + 1:02d}{slot_no}{c:02d}",
                        course_name=f"{dept.name} course {c + 1}",
                        department=dept,
                    )
                    session.course_teachers.add(rng.choice(faculty_by_dept[dept.id]))
                    for r in range(2):
                        students = rng.randint(20, 90)
                        label = f"R{c * 2 + r + 1:02d}"
                        if label not in inventory:
                            inventory[label], _ = Room.objects.get_or_create(
                                building=building, name=label, defaults={"capacity": 90}
                            )
                        rooms.append(
                            RoomAllocation(
                                session=session,
                                room=label,
                                room_ref=inventory[label],
                                student_count=students,
                                invigilators_required=required_invigilators(students, period.settings),
                            )
                        )
        RoomAllocation.objects.bulk_create(rooms)
        seats = sum(r.invigilators_required for r in rooms)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(departments)} departments, {n} faculty and '{name}' "
                f"with 20 time slots, {len(rooms)} rooms and {seats} seats."
            )
        )
