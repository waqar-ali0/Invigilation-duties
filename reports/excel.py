import io

from openpyxl import Workbook
from openpyxl.styles import Font

from allocation.engine import fairness_stats
from faculty.models import Designation

from .queries import faculty_load, roster_rows


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
        ws.append(
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
            ]
        )
    for col, width in zip("ABCDEFGHI", (12, 8, 8, 14, 30, 22, 10, 10, 60)):
        ws.column_dimensions[col].width = width
    for cell in ws["A"][1:]:
        cell.number_format = "yyyy-mm-dd"
    return _save(wb)


def load_xlsx(period):
    wb, ws = _sheet("Faculty Load", ["Employee ID", "Name", "Department", "Designation", "Duties"])
    load = faculty_load(period)
    labels = dict(Designation.choices)
    for f, n in load:
        ws.append([f.employee_id, f.name, f.department.name, labels.get(f.designation, f.designation), n])
    stats = fairness_stats(n for _, n in load)
    ws.append([])
    for label, value in (("Min", stats.min), ("Max", stats.max), ("Average", stats.avg)):
        ws.append([label, None, None, None, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    for col, width in zip("ABCDE", (14, 30, 22, 22, 8)):
        ws.column_dimensions[col].width = width
    return _save(wb)
