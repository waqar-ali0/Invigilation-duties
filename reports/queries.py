from dataclasses import dataclass
from datetime import date, time

from django.db.models import Count, Prefetch, Q

from duties.models import Duty, DutyStatus
from exams.halls import hall_key, halls_by_key
from exams.models import RoomAllocation
from faculty.models import Faculty


@dataclass(frozen=True)
class RosterRow:
    date: date
    start_time: time
    end_time: time
    course_code: str
    course_name: str
    department: str
    room: str
    student_count: int
    invigilators: list[str]


def roster_rows(period):
    """One row per room of `period`, by date, start time, course and room. Every course in a
    shared hall lists the hall's whole team."""
    duties = Duty.objects.active().select_related("faculty")
    rooms = list(
        RoomAllocation.objects.filter(session__slot__period=period)
        .select_related("session__slot", "session__department")
        .prefetch_related(Prefetch("duties", queryset=duties))
        .order_by("session__slot__date", "session__slot__start_time", "session__course_code", "room")
    )
    team = {key: sorted(d.faculty.name for r in rows for d in r.duties.all()) for key, rows in halls_by_key(rooms).items()}
    return [
        RosterRow(
            date=r.session.slot.date,
            start_time=r.session.slot.start_time,
            end_time=r.session.slot.end_time,
            course_code=r.session.course_code,
            course_name=r.session.course_name,
            department=r.session.department.name,
            room=r.room,
            student_count=r.student_count,
            invigilators=team[hall_key(r)],
        )
        for r in rooms
    ]


def faculty_load(period):
    """[(faculty, active duty count in `period`)] for every eligible faculty member, plus anyone
    no longer eligible who still has active duties in `period` (so the load matches the roster), by name."""
    faculty = (
        Faculty.objects.select_related("department")
        .annotate(
            n=Count("duties", filter=Q(duties__period=period) & ~Q(duties__status=DutyStatus.CANCELLED))
        )
        .filter(Q(is_active=True, is_exempt=False) | Q(n__gt=0))
        .order_by("name")
    )
    return [(f, f.n) for f in faculty]
