"""One faculty member's own duties: listing and downloads."""

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
