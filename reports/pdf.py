import io
from itertools import groupby

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from exams.models import PeriodStatus

from .queries import roster_rows


def roster_pdf(period):
    """A4 roster: one section per date and time slot, one table row per room."""
    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    cell.fontSize = 9
    cell.leading = 11

    title = f"Invigilation Roster — {period.name}"
    if period.status not in (PeriodStatus.PUBLISHED, PeriodStatus.CLOSED):
        title += " (DRAFT)"

    story = [Paragraph(title, styles["Title"])]
    rows = roster_rows(period)
    if not rows:
        story.append(Paragraph("No exams scheduled.", styles["BodyText"]))

    for (day, start, end), group in groupby(rows, key=lambda r: (r.date, r.start_time, r.end_time)):
        heading = Paragraph(f"{day:%A %d %B %Y}, {start:%H:%M}–{end:%H:%M}", styles["Heading3"])
        data = [["Course", "Room", "Students", "Invigilators"]]
        for r in group:
            data.append(
                [
                    Paragraph(f"{r.course_code} — {r.course_name}", cell),
                    r.room,
                    str(r.student_count),
                    Paragraph(", ".join(r.invigilators) or "—", cell),
                ]
            )
        table = Table(data, colWidths=[60 * mm, 20 * mm, 18 * mm, 82 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7ecf5")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c0cc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(KeepTogether([heading, table]))
        story.append(Spacer(1, 4 * mm))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=title, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm
    )
    doc.build(story)
    return buf.getvalue()
