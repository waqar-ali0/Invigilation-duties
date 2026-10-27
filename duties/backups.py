"""Backup pool: who is kept free in each time slot, and the record of who was called upon."""

from collections import Counter, defaultdict

from .models import BackupMember, BackupReservation, Duty, ReservationStatus, Unavailability, UnavailabilityStatus


def _busy_by_slot(period):
    """{slot id: faculty ids holding a duty in that slot or one overlapping it}."""
    slots = list(period.slots.all())
    holders = defaultdict(set)
    for faculty_id, slot_id in Duty.objects.active().filter(period=period).values_list("faculty_id", "room_allocation__session__slot_id"):
        holders[slot_id].add(faculty_id)
    busy = {}
    for s in slots:
        people = set(holders[s.id])
        for o in slots:
            if o.id != s.id and o.date == s.date and o.start_time < s.end_time and s.start_time < o.end_time:
                people |= holders[o.id]
        busy[s.id] = people
    return busy


def _unavailable(period):
    days, slots = set(), set()
    for faculty_id, day, slot_id in Unavailability.objects.filter(period=period, status=UnavailabilityStatus.APPROVED).values_list(
        "faculty_id", "date", "slot_id"
    ):
        (days.add((faculty_id, day)) if slot_id is None else slots.add((faculty_id, slot_id)))
    return days, slots


def reserve_backups(period):
    """Keep `backups_per_slot` backup members free in every slot of `period`.

    Reservations of people who have since become busy or unavailable are released; free places
    go to the pool by priority, then fewest reservations, never to someone busy at that time.
    Reservations already called upon are left alone. Returns the number of reservations made."""
    wanted = period.settings.backups_per_slot
    busy = _busy_by_slot(period)
    off_days, off_slots = _unavailable(period)
    current = list(BackupReservation.objects.filter(period=period, status=ReservationStatus.RESERVED).select_related("slot"))
    for r in current:
        if wanted == 0 or r.faculty_id in busy.get(r.slot_id, ()) or (r.faculty_id, r.slot.date) in off_days or (r.faculty_id, r.slot_id) in off_slots:
            r.delete()
    if wanted == 0:
        return 0
    members = list(
        BackupMember.objects.filter(faculty__is_active=True, faculty__is_exempt=False).select_related("faculty")
    )
    held = Counter(BackupReservation.objects.filter(period=period).exclude(status=ReservationStatus.RELEASED).values_list("faculty_id", flat=True))
    taken = defaultdict(set)
    for slot_id, faculty_id in BackupReservation.objects.filter(period=period).exclude(status=ReservationStatus.RELEASED).values_list("slot_id", "faculty_id"):
        taken[slot_id].add(faculty_id)
    made = 0
    for slot in period.slots.order_by("date", "start_time"):
        free = wanted - len(taken[slot.id])
        if free <= 0:
            continue
        options = [
            m for m in members
            if m.faculty_id not in taken[slot.id]
            and m.faculty_id not in busy.get(slot.id, ())
            and (m.faculty_id, slot.date) not in off_days
            and (m.faculty_id, slot.id) not in off_slots
            and (m.max_backup is None or held[m.faculty_id] < m.max_backup)
        ]
        options.sort(key=lambda m: (m.priority, held[m.faculty_id], m.faculty.name))
        for m in options[:free]:
            BackupReservation.objects.create(period=period, slot=slot, faculty_id=m.faculty_id)
            taken[slot.id].add(m.faculty_id)
            held[m.faculty_id] += 1
            made += 1
    return made


def backup_order(slot_id, building_id=None):
    """{faculty id: sort rank} for backups: reserved for this slot first, then by pool priority.
    Members limited to other buildings rank after the rest."""
    ranks = {}
    for faculty_id in BackupReservation.objects.filter(slot_id=slot_id, status=ReservationStatus.RESERVED).values_list("faculty_id", flat=True):
        ranks[faculty_id] = (0, 0)
    for m in BackupMember.objects.prefetch_related("buildings"):
        if m.faculty_id in ranks:
            continue
        wrong_building = building_id is not None and m.buildings.exists() and not any(b.pk == building_id for b in m.buildings.all())
        ranks[m.faculty_id] = (2 if wrong_building else 1, m.priority)
    return ranks


def record_call(old_duty, new_duty):
    """When the person taking over `old_duty` was reserved as backup for that slot, mark the
    reservation as called and link the duty they took."""
    slot_id = old_duty.room_allocation.session.slot_id
    BackupReservation.objects.filter(slot_id=slot_id, faculty_id=new_duty.faculty_id, status=ReservationStatus.RESERVED).update(
        status=ReservationStatus.CALLED, duty=new_duty
    )
