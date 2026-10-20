import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook


def make_xlsx(rows, name="upload.xlsx"):
    """An uploaded .xlsx whose first sheet holds `rows` (first row = header)."""
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


def make_csv(text, name="upload.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8-sig"))


SCHEDULE_HEADER = [
    "Date",
    "Start Time",
    "End Time",
    "Course Code",
    "Course Name",
    "Department",
    "Room",
    "Student Count",
    "Course Teacher Emails",
]
FACULTY_HEADER = ["Employee ID", "Name", "Email", "Department", "Designation"]
