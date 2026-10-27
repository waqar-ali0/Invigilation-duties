"""Shared halls: courses that sit in the same room in the same time slot share one team of invigilators.

The hall's first row (lowest id) carries the whole team, sized from all the hall's students; the
other rows need nobody. Room names are compared ignoring case and extra spaces.
"""

from collections import defaultdict

from duties.models import Duty

from .models import RoomAllocation, required_invigilators, room_key


def hall_key(room):
    return (room.session.slot_id, room_key(room.room))


def hall_rooms(room):
    """Every row in `room`'s hall (including `room`), first row first."""
    same_slot = RoomAllocation.objects.filter(session__slot_id=room.session.slot_id).select_related("session").order_by("pk")
    rows = [r for r in same_slot if hall_key(r) == hall_key(room)]
    return rows or [room]


def hall_lead(room):
    """The row that holds the invigilators for `room`'s hall."""
    return hall_rooms(room)[0]


def halls_by_key(rooms):
    """{hall key: [rows, first row first]} for an iterable of rows with `session` loaded."""
    halls = defaultdict(list)
    for r in sorted(rooms, key=lambda r: r.pk):
        halls[hall_key(r)].append(r)
    return halls


def staff_shared_halls(period):
    """Size every shared hall of a draft `period` from its total students, put the whole team on the
    hall's first row and move any duties from the other rows onto it. Rooms used by one course
    keep their own number (unless an earlier sharing left it at 0). Returns the number of shared halls."""
    rooms = RoomAllocation.objects.filter(session__slot__period=period).select_related("session", "room_ref")
    settings = period.settings
    changed, shared = [], 0
    for lead, *others in halls_by_key(rooms).values():
        inventory = next((r.room_ref for r in [lead, *others] if r.room_ref_id), None)
        if others:
            shared += 1
            total = sum(r.student_count for r in [lead, *others])
            targets = [(lead, required_invigilators(total, settings, inventory))]
            targets += [(r, 0) for r in others]
            Duty.objects.filter(room_allocation__in=others).update(room_allocation=lead)
        elif lead.invigilators_required == 0:
            targets = [(lead, required_invigilators(lead.student_count, settings, inventory))]
        else:
            targets = []
        for room, needed in targets:
            if room.invigilators_required != needed:
                room.invigilators_required = needed
                changed.append(room)
    RoomAllocation.objects.bulk_update(changed, ["invigilators_required"])
    return shared
