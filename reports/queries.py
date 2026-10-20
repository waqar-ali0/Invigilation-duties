from django.db.models import Count, Q

from duties.models import DutyStatus
from faculty.models import Faculty


def faculty_load(period):
    """[(faculty, active duty count in `period`)] for every eligible faculty member, by name."""
    faculty = (
        Faculty.objects.eligible()
        .select_related("department")
        .annotate(
            n=Count("duties", filter=Q(duties__period=period) & ~Q(duties__status=DutyStatus.CANCELLED))
        )
        .order_by("name")
    )
    return [(f, f.n) for f in faculty]
