import io

from openpyxl import Workbook
from openpyxl.styles import Font

from allocation.engine import fairness_stats
from faculty.models import Designation

from .queries import faculty_load, roster_rows


FORMULA_PREFIXES = ("=", "+", "-", "@")


def _append(ws, values):
    """Append a row, quoting text that a spreadsheet would otherwise run as a formula."""
    ws.append(["'" + v if isinstance(v, str) and v.startswith(FORMULA_PREFIXES) else v for v in values])


def _save(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet(title, header):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    return wb, ws


def roster_xlsx(period):
    header = ["Date", "Start", "End", "Course Code", "Course Name", "Department", "Room", "Students", "Invigilators"]
    wb, ws = _sheet("Roster", header)
    for r in roster_rows(period):
        _append(
            ws,
            [
                r.date,
                r.start_time.strftime("%H:%M"),
                r.end_time.strftime("%H:%M"),
                r.course_code,
                r.course_name,
                r.department,
                r.room,
                r.student_count,
                ", ".join(r.invigilators),
            ],
        )
    for col, width in zip("ABCDEFGHI", (12, 8, 8, 14, 30, 22, 10, 10, 60)):
        ws.column_dimensions[col].width = width
    for cell in ws["A"][1:]:
        cell.number_format = "yyyy-mm-dd"
    return _save(wb)


def department_xlsx(period, departments):
    """Duties of every faculty member in `departments` for `period`."""
    wb, ws = _sheet("Department Duties", ["Name", "Department", "Duties", "Dates and rooms"])
    for row in department_rows(period, departments):
        _append(ws, [row["faculty"].name, row["faculty"].department.name, len(row["duties"]), row["summary"]])
    for col, width in zip("ABCD", (30, 22, 8, 90)):
        ws.column_dimensions[col].width = width
    return _save(wb)


def department_rows(period, departments):
    """[{faculty, duties, summary}] for active faculty of `departments`, by name."""
    from faculty.models import Faculty

    from .personal import faculty_duties

    rows = []
    for f in Faculty.objects.filter(department__in=departments, is_active=True).select_related("department").order_by("name"):
        duties = faculty_duties(f, period)
        summary = "; ".join(
            f"{d.room_allocation.session.slot.date:%d %b} {d.room_allocation.session.slot.start_time:%H:%M} {d.room_allocation.room}"
            for d in duties
        )
        rows.append({"faculty": f, "duties": duties, "summary": summary})
    return rows


def load_xlsx(period):
    wb, ws = _sheet("Faculty Load", ["Employee ID", "Name", "Department", "Designation", "Duties"])
    load = faculty_load(period)
    labels = dict(Designation.choices)
    for f, n in load:
        _append(ws, [f.employee_id, f.name, f.department.name, labels.get(f.designation, f.designation), n])
    stats = fairness_stats(n for f, n in load if f.is_active and not f.is_exempt)
    ws.append([])
    for label, value in (("Min", stats.min), ("Max", stats.max), ("Average", stats.avg)):
        ws.append([label, None, None, None, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    for col, width in zip("ABCDE", (14, 30, 22, 22, 8)):
        ws.column_dimensions[col].width = width
    return _save(wb)
