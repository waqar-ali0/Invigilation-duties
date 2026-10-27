from datetime import date, datetime, time

from django.db import transaction

from duties.models import Duty
from faculty.models import Department, Faculty

from ..halls import staff_shared_halls
from ..models import Building, ExamPeriod, ExamSession, Room, RoomAllocation, TimeSlot, required_invigilators, room_key
from .reader import read_rows
from .result import ImportResult, is_blank, length_errors, missing_columns

COLUMNS = ["date", "start_time", "end_time", "course_code", "course_name", "department", "room", "student_count"]
LIMITS = {
    "course_code": (ExamSession, "course_code"),
    "course_name": (ExamSession, "course_name"),
    "room": (RoomAllocation, "room"),
}
DUTIES_EXIST = "This period already has duties. Clear the draft before re-importing the schedule."
# Excel-exported CSVs can carry a midnight time on dates and AM/PM times.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y", "%d.%m.%Y")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p")


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    return None


def _parse_time(value):
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(str(value).strip().upper(), fmt).time()
        except ValueError:
            pass
    return None


def _parse_count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return int(text) if text.isdigit() else None


class RoomFinder:
    """Matches a schedule's room (and optional building) to the room inventory."""

    def __init__(self):
        self.rooms = list(Room.objects.filter(is_active=True).select_related("building"))
        self.buildings = {" ".join(b.name.split()).lower(): b for b in Building.objects.all()}

    def find(self, room, building=""):
        """(inventory room or None, error message or "")."""
        matches = [r for r in self.rooms if room_key(r.name) == room_key(room)]
        if building:
            matches = [r for r in matches if room_key(r.building.name) == room_key(building)]
        if len(matches) > 1:
            return None, f"room '{room}' exists in several buildings; add a building column"
        return (matches[0] if matches else None), ""


def check_rows(period, rows):
    """Validate schedule rows (dicts keyed by column name, with "_row").

    Returns (errors {row number: [messages]}, warnings {row number: [messages]}, records).
    The file replaces the period's current schedule, so slots are only compared within the file.
    """
    departments = {d.name.lower(): d for d in Department.objects.all()}
    teachers = {}
    for pk, email, emp in Faculty.objects.values_list("pk", "email", "employee_id"):
        teachers[emp.lower()] = pk
        if email:
            teachers[email.lower()] = pk
    finder = RoomFinder()
    slot_ends, seen = {}, {}
    errors, warnings, records = {}, {}, []

    for row in rows:
        n = row["_row"]
        row_errors = [f"{c} is required" for c in COLUMNS if is_blank(row.get(c))]
        row_errors += [e.split(": ", 1)[1] for e in length_errors(n, row, LIMITS)]
        row_warnings = []

        day = start = end = None
        if not is_blank(row.get("date")):
            day = _parse_date(row["date"])
            if day is None:
                row_errors.append(f"invalid date '{row['date']}'")
            elif not (period.start_date <= day <= period.end_date):
                row_errors.append(f"date {day.isoformat()} is outside the exam period")
        for col in ("start_time", "end_time"):
            if not is_blank(row.get(col)):
                parsed = _parse_time(row[col])
                if parsed is None:
                    row_errors.append(f"invalid time '{row[col]}'")
                elif col == "start_time":
                    start = parsed
                else:
                    end = parsed
        if start and end:
            if end <= start:
                row_errors.append("end time must be after start time")
            elif day:
                known_end = slot_ends.setdefault((day, start), end)
                if known_end != end:
                    row_errors.append(f"slot at {day.isoformat()} {start:%H:%M} already exists with a different end time")

        dept = None
        if not is_blank(row.get("department")):
            dept = departments.get(str(row["department"]).strip().lower())
            if dept is None:
                row_errors.append(f"unknown department '{row['department']}'")

        count = None
        if not is_blank(row.get("student_count")):
            count = _parse_count(row["student_count"])
            if count is None:
                row_errors.append("student count must be a whole number of 0 or more")

        teacher_ids = []
        for token in str(row.get("course_teacher_emails") or "").replace(",", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            if token.lower() in teachers:
                teacher_ids.append(teachers[token.lower()])
            else:
                row_errors.append(f"unknown teacher '{token}'")

        code = "" if is_blank(row.get("course_code")) else str(row["course_code"]).strip()
        room = "" if is_blank(row.get("room")) else " ".join(str(row["room"]).split())
        building = "" if is_blank(row.get("building")) else str(row["building"]).strip()
        inventory = None
        if room:
            inventory, problem = finder.find(room, building)
            if problem:
                row_errors.append(problem)
            elif inventory is None:
                row_warnings.append(f"room '{room}' is not in the room list")
            elif count is not None and inventory.capacity is not None and count > inventory.capacity:
                row_warnings.append(f"{count} students but room {inventory.name} seats {inventory.capacity}")
        if day and start and code and room:
            key = (day, start, code.lower(), room_key(room))
            if key in seen:
                row_errors.append(f"duplicate of row {seen[key]}")
            else:
                seen[key] = n

        if row_errors:
            errors[n] = row_errors
        else:
            records.append((day, start, end, code, str(row["course_name"]).strip(), dept, room, count, teacher_ids, inventory, building, n))
        if row_warnings:
            warnings[n] = row_warnings
    return errors, warnings, records


def save_schedule(period, records, add_rooms_to=None):
    """Replace the period's schedule with `records` (from check_rows). Rooms missing from the
    inventory are added to building `add_rooms_to` (name) when given. Returns rows created."""
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        if Duty.objects.filter(period=period).exists():
            raise ValueError(DUTIES_EXIST)
        period.slots.all().delete()  # replaces the old schedule (sessions and rooms cascade)
        new_rooms = {}
        for day, start, end, code, name, dept, room, count, teacher_ids, inventory, building, _ in records:
            if inventory is None and add_rooms_to:
                b_name = building or add_rooms_to
                key = (room_key(b_name), room_key(room))
                if key not in new_rooms:
                    b = Building.objects.filter(name__iexact=b_name).first() or Building.objects.create(name=b_name)
                    new_rooms[key] = Room.objects.filter(building=b, name__iexact=room).first() or Room.objects.create(
                        building=b, name=room
                    )
                inventory = new_rooms[key]
            slot, _ = TimeSlot.objects.get_or_create(period=period, date=day, start_time=start, defaults={"end_time": end})
            session, _ = ExamSession.objects.get_or_create(
                slot=slot, course_code=code, defaults={"course_name": name, "department": dept}
            )
            if teacher_ids:
                session.course_teachers.add(*teacher_ids)
            RoomAllocation.objects.create(
                session=session,
                room=room,
                room_ref=inventory,
                student_count=count,
                invigilators_required=required_invigilators(count, period.settings, inventory),
            )
        staff_shared_halls(period)
    return len(records)


def import_schedule(period, file):
    """Columns: date, start_time, end_time, course_code, course_name, department, room,
    student_count, and optionally building and course_teacher_emails (separated by ';')."""
    period.ensure_editable()
    if Duty.objects.filter(period=period).exists():
        return ImportResult(errors=[DUTIES_EXIST])
    rows = read_rows(file)
    if problem := missing_columns(rows, COLUMNS):
        return problem
    errors, _, records = check_rows(period, rows)
    if errors:
        return ImportResult(errors=[f"Row {n}: {m}" for n in sorted(errors) for m in errors[n]])
    try:
        created = save_schedule(period, records)
    except ValueError as e:
        return ImportResult(errors=[str(e)])
    return ImportResult(created=created)
