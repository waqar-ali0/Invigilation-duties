from datetime import date, datetime, time

from django.db import transaction

from duties.models import Duty
from faculty.models import Department, Faculty

from ..models import ExamPeriod, ExamSession, RoomAllocation, TimeSlot, required_invigilators
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
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p")


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value), fmt).date()
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
            return datetime.strptime(str(value), fmt).time()
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
    return int(text) if text.isdigit() else None


def import_schedule(period, file):
    """Columns: date, start_time, end_time, course_code, course_name, department, room,
    student_count, and optionally course_teacher_emails (separated by ';')."""
    period.ensure_editable()
    if Duty.objects.filter(period=period).exists():
        return ImportResult(errors=[DUTIES_EXIST])

    rows = read_rows(file)
    if problem := missing_columns(rows, COLUMNS):
        return problem

    departments = {d.name.lower(): d for d in Department.objects.all()}
    teachers = {e.lower(): pk for pk, e in Faculty.objects.values_list("pk", "email")}
    # The file replaces the period's current schedule, so slots are only compared within the file.
    slot_ends = {}
    seen = {}
    errors, records = [], []

    for row in rows:
        n = row["_row"]
        row_errors = [f"Row {n}: {c} is required" for c in COLUMNS if is_blank(row[c])]
        row_errors += length_errors(n, row, LIMITS)

        day = start = end = None
        if not is_blank(row["date"]):
            day = _parse_date(row["date"])
            if day is None:
                row_errors.append(f"Row {n}: invalid date '{row['date']}'")
            elif not (period.start_date <= day <= period.end_date):
                row_errors.append(f"Row {n}: date {day.isoformat()} is outside the exam period")
        for col in ("start_time", "end_time"):
            if not is_blank(row[col]):
                parsed = _parse_time(row[col])
                if parsed is None:
                    row_errors.append(f"Row {n}: invalid time '{row[col]}'")
                elif col == "start_time":
                    start = parsed
                else:
                    end = parsed
        if start and end:
            if end <= start:
                row_errors.append(f"Row {n}: end time must be after start time")
            elif day:
                known_end = slot_ends.setdefault((day, start), end)
                if known_end != end:
                    row_errors.append(
                        f"Row {n}: slot at {day.isoformat()} {start:%H:%M} already exists with a different end time"
                    )

        dept = None
        if not is_blank(row["department"]):
            dept = departments.get(str(row["department"]).lower())
            if dept is None:
                row_errors.append(f"Row {n}: unknown department '{row['department']}'")

        count = None
        if not is_blank(row["student_count"]):
            count = _parse_count(row["student_count"])
            if count is None:
                row_errors.append(f"Row {n}: student count must be a whole number of 0 or more")

        teacher_ids = []
        for email in str(row.get("course_teacher_emails") or "").split(";"):
            email = email.strip()
            if not email:
                continue
            if email.lower() in teachers:
                teacher_ids.append(teachers[email.lower()])
            else:
                row_errors.append(f"Row {n}: unknown teacher email '{email}'")

        code = "" if is_blank(row["course_code"]) else str(row["course_code"])
        room = "" if is_blank(row["room"]) else str(row["room"])
        if day and start and code and room:
            key = (day, start, code.lower(), room.lower())
            if key in seen:
                row_errors.append(f"Row {n}: duplicate of row {seen[key]}")
            else:
                seen[key] = n

        errors.extend(row_errors)
        if not row_errors:
            records.append((day, start, end, code, str(row["course_name"]), dept, room, count, teacher_ids))

    if errors:
        return ImportResult(errors=errors)

    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        if Duty.objects.filter(period=period).exists():
            return ImportResult(errors=[DUTIES_EXIST])
        period.slots.all().delete()  # replaces the old schedule (sessions and rooms cascade)
        for day, start, end, code, name, dept, room, count, teacher_ids in records:
            slot, _ = TimeSlot.objects.get_or_create(
                period=period, date=day, start_time=start, defaults={"end_time": end}
            )
            session, _ = ExamSession.objects.get_or_create(
                slot=slot, course_code=code, defaults={"course_name": name, "department": dept}
            )
            if teacher_ids:
                session.course_teachers.add(*teacher_ids)
            RoomAllocation.objects.create(
                session=session,
                room=room,
                student_count=count,
                invigilators_required=required_invigilators(count, period.settings),
            )
    return ImportResult(created=len(records))
