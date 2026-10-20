"""One faculty member's own duties: listing and downloads."""

import io
from datetime import datetime, timezone as dt_timezone

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from duties.models import Duty
from exams.models import PeriodStatus

VISIBLE_STATUSES = (PeriodStatus.PUBLISHED, PeriodStatus.CLOSED)


def faculty_duties(faculty, period=None):
    """Active duties of `faculty` in published or closed periods (one period, or all), by date and time."""
    duties = (
        Duty.objects.active()
        .filter(faculty=faculty, period__status__in=VISIBLE_STATUSES)
        .select_related("period", "room_allocation__session__slot")
        .order_by("room_allocation__session__slot__date", "room_allocation__session__slot__start_time")
    )
    if period is not None:
        duties = duties.filter(period=period)
    return list(duties)


def duties_pdf(faculty, period):
    styles = getSampleStyleSheet()
    title = f"Invigilation Duties — {faculty.name} — {period.name}"
    data = [["Date", "Time", "Course", "Room"]]
    for d in faculty_duties(faculty, period):
        s = d.room_allocation.session
        data.append(
            [
                f"{s.slot.date:%a %d %b %Y}",
                f"{s.slot.start_time:%H:%M}–{s.slot.end_time:%H:%M}",
                Paragraph(f"{s.course_code} — {s.course_name}", styles["BodyText"]),
                d.room_allocation.room,
            ]
        )
    table = Table(data, colWidths=[35 * mm, 28 * mm, 90 * mm, 27 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7ecf5")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c0cc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story = [Paragraph(title, styles["Title"]), table if len(data) > 1 else Paragraph("No duties.", styles["BodyText"])]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, title=title, leftMargin=15 * mm, rightMargin=15 * mm).build(story)
    return buf.getvalue()


def _ics_text(value):
    return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_time(day, t):
    local = timezone.make_aware(datetime.combine(day, t))
    return local.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def duties_ics(faculty, duties):
    """An iCalendar file with one event per duty (times in UTC, converted from local exam times)."""
    stamp = timezone.now().astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Invigilation Duties//EN", "CALSCALE:GREGORIAN"]
    for d in duties:
        s = d.room_allocation.session
        lines += [
            "BEGIN:VEVENT",
            f"UID:duty-{d.pk}@invigilation",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_ics_time(s.slot.date, s.slot.start_time)}",
            f"DTEND:{_ics_time(s.slot.date, s.slot.end_time)}",
            f"SUMMARY:{_ics_text('Invigilation: ' + s.course_code)}",
            f"LOCATION:{_ics_text(d.room_allocation.room)}",
            f"DESCRIPTION:{_ics_text(s.course_name)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
