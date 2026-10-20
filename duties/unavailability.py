"""Faculty requests for dates they cannot invigilate, and decisions on them."""

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Unavailability, UnavailabilityStatus

MSG_WINDOW_CLOSED = "The availability window for this period is closed."


class RequestRejected(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def submit_request(faculty, period, day, slot, reason):
    """Create a pending request for `day` (whole day when `slot` is None)."""
    reason = (reason or "").strip()
    errors = []
    if faculty.is_exempt:
        errors.append("You are exempt from invigilation.")
    if not period.availability_is_open:
        errors.append(MSG_WINDOW_CLOSED)
    if not (period.start_date <= day <= period.end_date):
        errors.append("Date is outside the exam period.")
    if slot is not None and (slot.period_id != period.id or slot.date != day):
        errors.append("That time slot is not on the chosen date.")
    if not reason:
        errors.append("A reason is required.")
    overlapping = Unavailability.objects.filter(
        faculty=faculty, date=day, status__in=[UnavailabilityStatus.PENDING, UnavailabilityStatus.APPROVED]
    )
    if slot is not None:
        overlapping = overlapping.filter(Q(slot__isnull=True) | Q(slot=slot))
    if overlapping.exists():
        errors.append("You already have a request covering this date and slot.")
    if errors:
        raise RequestRejected(errors)
    return Unavailability.objects.create(faculty=faculty, period=period, date=day, slot=slot, reason=reason)


def withdraw_request(req, faculty):
    if req.faculty_id != faculty.id:
        raise PermissionDenied
    if req.status != UnavailabilityStatus.PENDING:
        raise RequestRejected(["Only pending requests can be withdrawn."])
    if not req.period.availability_is_open:
        raise RequestRejected([MSG_WINDOW_CLOSED])
    req.status = UnavailabilityStatus.WITHDRAWN
    req.save(update_fields=["status"])
