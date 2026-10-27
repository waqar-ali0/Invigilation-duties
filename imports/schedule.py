"""Exam schedule import for one period: any column layout, checked and previewed before it replaces the schedule."""

from audit.log import log
from duties.models import Duty
from exams.importers.schedule import DUTIES_EXIST, check_rows, save_schedule

from .base import Field, ImportKind, Option, Plan, RowPlan, Status


class ScheduleImport(ImportKind):
    key = "schedule"
    title = "Exam schedule"
    description = (
        "One row per course per room. Importing replaces this period's whole schedule, so every "
        "row must be correct before it can be confirmed. Rooms are matched to the room list."
    )
    needs_period = True
    selectable = False
    modes = (("replace", "Replace this period's schedule with the file"),)
    fields = (
        Field("date", "Date", required=True, synonyms=("exam date", "day")),
        Field("start_time", "Start time", required=True, synonyms=("start", "from", "time from", "begins")),
        Field("end_time", "End time", required=True, synonyms=("end", "to", "time to", "ends")),
        Field("course_code", "Course code", required=True, synonyms=("code", "course", "subject code")),
        Field("course_name", "Course name", required=True, synonyms=("course title", "subject", "title", "name")),
        Field("department", "Department", required=True, synonyms=("dept",)),
        Field("building", "Building", synonyms=("block",)),
        Field("room", "Room", required=True, synonyms=("room no", "room number", "venue", "hall")),
        Field("student_count", "Students", required=True, synonyms=("student count", "no of students", "students count", "strength", "enrolled")),
        Field("course_teacher_emails", "Course teachers", synonyms=("teachers", "teacher", "course teacher", "course teacher emails", "instructor")),
    )
    options = (
        Option("add_rooms", "Add rooms that are not in the room list", default=True,
               help="They are added to the row's building, or to the building below."),
        Option("default_building", "Building for new rooms without one", "text", default="Main Building"),
    )

    def _rows(self, batch):
        rows = []
        for number, cells in batch.rows:
            row = self.values(batch, cells)
            row["_row"] = number
            rows.append(row)
        return rows

    def plan(self, batch):
        period = batch.period
        errors, warnings, records = check_rows(period, self._rows(batch))
        ok = {r[-1] for r in records}
        rows = []
        for row in self._rows(batch):
            n = row["_row"]
            label = f"{row['course_code']} — {row['room']}" if row["course_code"] else f"Row {n}"
            p = RowPlan(n, row, label=label, errors=list(errors.get(n, [])), warnings=list(warnings.get(n, [])))
            p.status = Status.NEW if n in ok else Status.INVALID
            rows.append(p.finish())
        plan = Plan(rows)
        plan.records = records
        if Duty.objects.filter(period=period).exists():
            plan.blocking = DUTIES_EXIST
        elif any(p.status == Status.INVALID for p in rows):
            plan.blocking = "Every row must be correct before the schedule can replace the current one. Fix the rows marked below (download the error report) and upload again."
        elif period.slots.exists():
            plan.notes.append(f"This replaces the current schedule of {period.name}.")
        return plan

    def commit(self, batch, selected, decisions, mode, actor):
        plan = self.plan(batch)
        if plan.blocking:
            raise ValueError(plan.blocking)
        add_to = (batch.options.get("default_building") or "Main Building").strip() if batch.options.get("add_rooms") else None
        created = save_schedule(batch.period, plan.records, add_rooms_to=add_to)
        log(actor, "import.schedule", batch.period, after={"file": batch.filename, "rows": created})
        return {"created": created, "updated": 0, "unchanged": 0, "skipped": 0, "invalid": 0}
