"""The invigilation allocation engine.

Pure Python with no database access: callers describe the exam period with the
dataclasses below, and get back the new assignments, the seats that could not be
filled (with a reason), and fairness statistics.
"""

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, time
from typing import Iterable

MSG_SAME_SLOT = "Already assigned in this time slot"
MSG_OVERLAPPING_SLOT = "Already assigned in an overlapping time slot"
MSG_UNAVAILABLE = "Marked unavailable for this date or slot"
MSG_PERIOD_CAP = "Reached period cap ({cap})"
MSG_DAILY_LIMIT = "Reached daily limit ({max_per_day})"
MSG_BACK_TO_BACK = "Back-to-back with another duty"
MSG_COURSE_TEACHER = "Teaches this course"
MSG_RESERVE = "Backup only (kept in reserve)"


@dataclass(frozen=True)
class Slot:
    id: int
    date: date
    order: int  # 0-based position among the slots on the same date, by start time
    start: time | None = None  # with `end`, used to find slots that overlap on the same date
    end: time | None = None

    def overlaps(self, other: "Slot") -> bool:
        if self.id == other.id:
            return True
        if self.date != other.date or None in (self.start, self.end, other.start, other.end):
            return False
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Room:
    id: int
    slot_id: int
    needed: int
    course_teacher_ids: frozenset[int] = frozenset()
    building_id: int | None = None


@dataclass(frozen=True)
class Person:
    id: int
    department_id: int
    cap: int | None  # effective maximum duties for the period; None = no maximum set
    minimum: int = 0  # filled first, before anyone above their minimum
    target: int | None = None  # their share: seats go to whoever is furthest below theirs
    prior: int = 0  # duties already counted elsewhere (periods whose duties count together)
    reserve: bool = False  # backup-only: used for seats nobody else can take, and only when allowed


@dataclass(frozen=True)
class Preference:
    """Soft wishes: seats on preferred dates/slots/buildings are favoured, avoided slots disfavoured."""

    dates: frozenset = frozenset()
    slots: frozenset = frozenset()
    avoid_slots: frozenset = frozenset()
    buildings: frozenset = frozenset()

    def misses(self, slot: "Slot", room: "Room") -> int:
        """0 when a seat in `room` suits every wish; one point per wish it goes against."""
        miss = 0
        if slot.id in self.avoid_slots:
            miss += 2
        if (self.dates or self.slots) and slot.date not in self.dates and slot.id not in self.slots:
            miss += 1
        if self.buildings and room.building_id not in self.buildings:
            miss += 1
        return miss


@dataclass(frozen=True)
class Rules:
    max_per_day: int
    avoid_back_to_back: bool
    exclude_course_teacher: bool
    reserves_fill_shortages: bool = False


@dataclass(frozen=True)
class EngineInput:
    slots: list[Slot]
    rooms: list[Room]
    people: list[Person]
    rules: Rules
    unavailable_days: frozenset[tuple[int, date]] = frozenset()  # (person_id, date)
    unavailable_slots: frozenset[tuple[int, int]] = frozenset()  # (person_id, slot_id)
    fixed: tuple[tuple[int, int], ...] = ()  # existing (person_id, room_id) duties, kept as they are
    preferences: dict = None  # person_id -> Preference


@dataclass(frozen=True)
class Unfilled:
    room_id: int
    count: int
    reason: str


@dataclass(frozen=True)
class FairnessStats:
    min: int
    max: int
    avg: float


@dataclass(frozen=True)
class AllocationResult:
    assignments: list[tuple[int, int]]  # new (person_id, room_id) pairs only
    unfilled: list[Unfilled]
    stats: FairnessStats
    soft_violations: int
    seed: int
    preferences_met: int = 0  # new seats that suited the holder's wishes
    preferences_total: int = 0  # new seats given to people who expressed wishes


def fairness_stats(counts: Iterable[int]) -> FairnessStats:
    counts = list(counts)
    if not counts:
        return FairnessStats(0, 0, 0.0)
    return FairnessStats(min(counts), max(counts), round(sum(counts) / len(counts), 2))


class AllocationState:
    """Who is assigned where, and the rule checks that depend on it."""

    def __init__(self, data: EngineInput) -> None:
        self.data = data
        self.rules = data.rules
        self.slots = {s.id: s for s in data.slots}
        self.rooms = {r.id: r for r in data.rooms}
        self.people = {p.id: p for p in data.people}
        self._counts = Counter()
        self._day_counts = Counter()  # (person_id, date)
        self._slot_people = defaultdict(set)  # slot_id -> person ids
        self._person_slots = defaultdict(set)  # person_id -> slot ids
        self._room_people = defaultdict(list)  # room_id -> person ids
        # slot_id -> ids of the other slots on the same date whose times intersect it
        self._overlapping = defaultdict(set)
        by_date = defaultdict(list)
        for s in data.slots:
            by_date[s.date].append(s)
        for same_day in by_date.values():
            for a in same_day:
                for b in same_day:
                    if a.id != b.id and a.overlaps(b):
                        self._overlapping[a.id].add(b.id)
        for person_id, room_id in data.fixed:
            if room_id in self.rooms:
                self.assign(person_id, room_id)

    def count(self, person_id: int) -> int:
        return self._counts[person_id]

    def total(self, person_id: int) -> int:
        """Duties that count toward the person's limits: this period plus any counted together with it."""
        person = self.people.get(person_id)
        return self._counts[person_id] + (person.prior if person else 0)

    def remaining(self, room_id: int) -> int:
        return max(0, self.rooms[room_id].needed - len(self._room_people[room_id]))

    def people_in_slot(self, slot_id: int) -> set[int]:
        """People already sitting in this slot or in one that overlaps it."""
        people = set(self._slot_people[slot_id])
        for other in self._overlapping[slot_id]:
            people |= self._slot_people[other]
        return people

    def departments_in_room(self, room_id: int) -> set[int]:
        return {self.people[p].department_id for p in self._room_people[room_id] if p in self.people}

    def is_unavailable(self, person_id: int, slot: Slot) -> bool:
        return (person_id, slot.date) in self.data.unavailable_days or (
            person_id,
            slot.id,
        ) in self.data.unavailable_slots

    def assign(self, person_id: int, room_id: int) -> None:
        slot = self.slots[self.rooms[room_id].slot_id]
        self._counts[person_id] += 1
        self._day_counts[(person_id, slot.date)] += 1
        self._slot_people[slot.id].add(person_id)
        self._person_slots[person_id].add(slot.id)
        self._room_people[room_id].append(person_id)

    def hard_errors(self, person_id: int, room_id: int) -> list[str]:
        room = self.rooms[room_id]
        slot = self.slots[room.slot_id]
        person = self.people.get(person_id)
        cap = person.cap if person else 0
        errors = []
        if person is None:
            return ["Not eligible for duties in this period"]
        if person_id in self._slot_people[slot.id]:
            errors.append(MSG_SAME_SLOT)
        elif self._person_slots[person_id] & self._overlapping[slot.id]:
            errors.append(MSG_OVERLAPPING_SLOT)
        if self.is_unavailable(person_id, slot):
            errors.append(MSG_UNAVAILABLE)
        if cap is not None and self.total(person_id) >= cap:
            errors.append(MSG_PERIOD_CAP.format(cap=cap))
        if self._day_counts[(person_id, slot.date)] >= self.rules.max_per_day:
            errors.append(MSG_DAILY_LIMIT.format(max_per_day=self.rules.max_per_day))
        return errors

    def soft_warnings(self, person_id: int, room_id: int) -> list[str]:
        room = self.rooms[room_id]
        slot = self.slots[room.slot_id]
        warnings = []
        if self.rules.avoid_back_to_back:
            for other_id in self._person_slots[person_id]:
                other = self.slots[other_id]
                if other.date == slot.date and abs(other.order - slot.order) == 1:
                    warnings.append(MSG_BACK_TO_BACK)
                    break
        if self.rules.exclude_course_teacher and person_id in room.course_teacher_ids:
            warnings.append(MSG_COURSE_TEACHER)
        return warnings

    def check(self, person_id: int, room_id: int) -> tuple[list[str], list[str]]:
        """(hard errors, soft warnings) for giving `person_id` a seat in `room_id`."""
        return self.hard_errors(person_id, room_id), self.soft_warnings(person_id, room_id)


@dataclass(frozen=True)
class CapacityReport:
    seats_needed: int
    capacity: int  # duties the eligible faculty can still take (backup-only members not counted)
    ok: bool
    slot_shortages: list[tuple[int, int, int]]  # (slot_id, seats needed in slot, people available in slot)


def _slot_demand(state: AllocationState) -> tuple[dict[int, int], dict[int, int]]:
    """Per slot: remaining seats, and people still able to sit in it (not unavailable, not already there)."""
    needed = Counter()
    for room in state.data.rooms:
        needed[room.slot_id] += state.remaining(room.id)
    available = {}
    for slot in state.data.slots:
        busy = state.people_in_slot(slot.id)
        available[slot.id] = sum(
            1 for p in state.data.people if not state.is_unavailable(p.id, slot) and p.id not in busy
        )
    return needed, available


def _attendable(state: AllocationState, person: Person, slots_by_date) -> int:
    """Most duties `person` could sit in this period: per date, the non-overlapping slots
    they are available for (greedy by end time), capped by the daily limit."""
    total = 0
    for slots in slots_by_date.values():
        free = [s for s in slots if not state.is_unavailable(person.id, s)]
        free.sort(key=lambda s: (s.end or time.max, s.order))
        picked = []
        for s in free:
            if not any(s.overlaps(p) for p in picked):
                picked.append(s)
        total += min(state.rules.max_per_day, len(picked))
    return total


def _room_left(state: AllocationState, person: Person) -> float:
    """Most duties `person` may have in this period under their maximum (inf when none is set)."""
    if person.cap is None:
        return float("inf")
    return person.cap - person.prior


def capacity_check(data: EngineInput) -> CapacityReport:
    """Can the remaining seats be filled at all? Checked before allocating.

    Each person counts for at most their cap and at most the slots they can actually
    attend (unavailability, overlapping exams and the daily limit), minus duties they already have.
    """
    state = AllocationState(data)
    seats_needed = sum(state.remaining(r.id) for r in data.rooms)
    slots_with_rooms = {r.slot_id for r in data.rooms}
    slots_by_date = defaultdict(list)
    for s in data.slots:
        if s.id in slots_with_rooms:
            slots_by_date[s.date].append(s)
    usable = [p for p in data.people if not p.reserve or data.rules.reserves_fill_shortages]
    capacity = int(sum(
        max(0, min(_attendable(state, p, slots_by_date), _room_left(state, p)) - state.count(p.id)) for p in usable
    ))
    needed, available = _slot_demand(state)
    shortages = [(s.id, needed[s.id], available[s.id]) for s in data.slots if needed[s.id] > available[s.id]]
    return CapacityReport(seats_needed, capacity, seats_needed <= capacity, shortages)


def _unfilled_reason(state: AllocationState, room: Room) -> str:
    slot = state.slots[room.slot_id]
    unavailable = at_cap = at_daily = 0
    busy = state.people_in_slot(slot.id)
    reserves_allowed = state.rules.reserves_fill_shortages
    others = [p for p in state.data.people if p.id not in busy]
    if not others:
        return "All faculty are already assigned in this time slot"
    reserve = 0
    for p in others:
        if state.is_unavailable(p.id, slot):
            unavailable += 1
        elif p.cap is not None and state.total(p.id) >= p.cap:
            at_cap += 1
        elif p.reserve and not reserves_allowed:
            reserve += 1
        else:
            at_daily += 1
    total = len(others)
    if unavailable == total:
        return "All remaining faculty are unavailable"
    if at_cap == total:
        return "All remaining faculty have reached their period cap"
    if at_daily == total:
        return "All remaining faculty have reached their daily limit"
    parts = [f"{unavailable} unavailable", f"{at_cap} at period cap", f"{at_daily} at daily limit"]
    if reserve:
        parts.append(f"{reserve} kept in reserve")
    return "No eligible faculty: " + ", ".join(parts)


def fair_share(data: EngineInput) -> float:
    """Duties per person if every seat (filled or not) were shared equally — the share used
    for people without a target of their own."""
    seats = sum(r.needed for r in data.rooms) + sum(p.prior for p in data.people)
    return seats / len(data.people) if data.people else 0.0


def allocate(data: EngineInput, seed: int) -> AllocationResult:
    """Fill every remaining seat, hardest time slots first. Each seat goes to the eligible
    person who most needs it: anyone below their minimum first, then whoever is furthest
    below their own target (or the fair share when they have none), fewest duties next;
    soft rules and department spread break ties before a seeded random tie-break."""
    state = AllocationState(data)
    regular = [p for p in data.people if not p.reserve]
    reserves = [p for p in data.people if p.reserve] if data.rules.reserves_fill_shortages else []
    default_share = fair_share(EngineInput(data.slots, data.rooms, regular or data.people, data.rules)) or 1.0
    preferences = data.preferences or {}
    no_wishes = Preference()

    def progress(p: Person) -> float:
        share = p.target if p.target is not None else default_share
        if share <= 0:
            return float("inf")
        return state.total(p.id) / share

    needed, available = _slot_demand(state)

    def difficulty(slot: Slot):
        ratio = float("inf") if available[slot.id] == 0 else needed[slot.id] / available[slot.id]
        return (-ratio, slot.date, slot.order)

    order = [p.id for p in data.people]
    random.Random(seed).shuffle(order)
    tie_break = {pid: i for i, pid in enumerate(order)}

    rooms_by_slot = defaultdict(list)
    for room in sorted(data.rooms, key=lambda r: r.id):
        rooms_by_slot[room.slot_id].append(room)

    assignments = []
    unfilled = []
    soft_violations = 0
    met = wished = 0
    for slot in sorted(data.slots, key=difficulty):
        for room in rooms_by_slot[slot.id]:
            missing = 0
            for _ in range(state.remaining(room.id)):
                depts = state.departments_in_room(room.id)
                best_key, best = None, None
                for pool in (regular, reserves):  # backup-only members only when nobody else can sit
                    for p in pool:
                        if state.hard_errors(p.id, room.id):
                            continue
                        share_progress = progress(p)
                        key = (
                            len(state.soft_warnings(p.id, room.id)),
                            0 if state.total(p.id) < p.minimum else 1,
                            int(share_progress * default_share + 1e-9),  # whole duties: fairness first
                            preferences.get(p.id, no_wishes).misses(slot, room),  # then wishes
                            round(share_progress, 6),
                            state.total(p.id),
                            1 if p.department_id in depts else 0,
                            tie_break[p.id],
                        )
                        if best_key is None or key < best_key:
                            best_key, best = key, p
                    if best is not None:
                        break
                if best is None:
                    missing += 1
                    continue
                soft_violations += best_key[0]
                if best.id in preferences:
                    wished += 1
                    met += 1 if best_key[3] == 0 else 0
                state.assign(best.id, room.id)
                assignments.append((best.id, room.id))
            if missing:
                unfilled.append(Unfilled(room.id, missing, _unfilled_reason(state, room)))

    stats = fairness_stats(state.count(p.id) for p in regular or data.people)
    return AllocationResult(assignments, unfilled, stats, soft_violations, seed, met, wished)
