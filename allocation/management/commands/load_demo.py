import random
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import DesignationCap, ExamPeriod, ExamSession, RoomAllocation, TimeSlot, required_invigilators
from faculty.models import Department, Designation, Faculty

PERIOD_NAME = "Demo Finals"
FIRST = ["Ayesha", "Bilal", "Fatima", "Hamza", "Hina", "Imran", "Kamran", "Maryam", "Nadia", "Omar",
         "Rabia", "Saad", "Sana", "Tariq", "Usman", "Zainab", "Ali", "Farah", "Junaid", "Mehwish"]
LAST = ["Ahmed", "Khan", "Malik", "Hussain", "Qureshi", "Shah", "Butt", "Siddiqui", "Raza", "Iqbal"]
# Designation mix for each department's ten faculty members.
MIX = [Designation.PROFESSOR] + [Designation.ASSOCIATE_PROFESSOR] * 2 + [Designation.ASSISTANT_PROFESSOR] * 3 + [
    Designation.LECTURER
] * 3 + [Designation.VISITING]
SLOT_TIMES = [(time(9), time(12)), (time(14), time(17))]


class Command(BaseCommand):
    help = "Create demo data: 20 departments, 200 faculty and a 10-day 'Demo Finals' exam period."

    @transaction.atomic
    def handle(self, *args, **options):
        if ExamPeriod.objects.filter(name=PERIOD_NAME).exists():
            raise CommandError(f"{PERIOD_NAME} already exists.")
        rng = random.Random(2026)

        departments = []
        for i in range(1, 21):
            dept, _ = Department.objects.get_or_create(name=f"Department {i:02d}")
            departments.append(dept)

        faculty_by_dept = {}
        exempt = set(rng.sample(range(200), 5))
        n = 0
        for dept in departments:
            people = []
            for designation in MIX:
                people.append(
                    Faculty(
                        employee_id=f"DEMO{n:03d}",
                        name=f"{rng.choice(FIRST)} {rng.choice(LAST)} {n:03d}",
                        email=f"demo{n:03d}@uni.edu",
                        department=dept,
                        designation=designation,
                        is_exempt=n in exempt,
                    )
                )
                n += 1
            faculty_by_dept[dept.id] = Faculty.objects.bulk_create(people)

        start = date(2026, 12, 1)
        period = ExamPeriod.objects.create(name=PERIOD_NAME, start_date=start, end_date=start + timedelta(days=9))
        DesignationCap.objects.create(period=period, designation=Designation.PROFESSOR, max_duties=4)

        rooms = []
        for day in range(10):
            for slot_no, (t1, t2) in enumerate(SLOT_TIMES):
                slot = TimeSlot.objects.create(period=period, date=start + timedelta(days=day), start_time=t1, end_time=t2)
                for c in range(15):
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
                        rooms.append(
                            RoomAllocation(
                                session=session,
                                room=f"R{c * 2 + r + 1:02d}",
                                student_count=students,
                                invigilators_required=required_invigilators(students, period.settings),
                            )
                        )
        RoomAllocation.objects.bulk_create(rooms)
        seats = sum(r.invigilators_required for r in rooms)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(departments)} departments, {n} faculty and '{PERIOD_NAME}' "
                f"with 20 time slots, {len(rooms)} rooms and {seats} seats."
            )
        )
