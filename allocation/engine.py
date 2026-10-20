"""The invigilation allocation engine.

Pure Python with no database access: callers describe the exam period with the
dataclasses below, and get back the new assignments, the seats that could not be
filled (with a reason), and fairness statistics.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

MSG_SAME_SLOT = "Already assigned in this time slot"
MSG_UNAVAILABLE = "Marked unavailable for this date or slot"
MSG_PERIOD_CAP = "Reached period cap ({cap})"
MSG_DAILY_LIMIT = "Reached daily limit ({max_per_day})"
MSG_BACK_TO_BACK = "Back-to-back with another duty"
MSG_COURSE_TEACHER = "Teaches this course"


@dataclass(frozen=True)
class Slot:
    id: int
    date: date
    order: int  # 0-based position among the slots on the same date, by start time


@dataclass(frozen=True)
class Room:
    id: int
    slot_id: int
    needed: int
    course_teacher_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Person:
    id: int
    department_id: int
    cap: int  # effective maximum duties for the period


@dataclass(frozen=True)
class Rules:
    max_per_day: int
    avoid_back_to_back: bool
    exclude_course_teacher: bool


@dataclass(frozen=True)
class EngineInput:
    slots: list[Slot]
    rooms: list[Room]
    people: list[Person]
    rules: Rules
    unavailable_days: frozenset[tuple[int, date]] = frozenset()  # (person_id, date)
    unavailable_slots: frozenset[tuple[int, int]] = frozenset()  # (person_id, slot_id)
    fixed: tuple[tuple[int, int], ...] = ()  # existing (person_id, room_id) duties, kept as they are


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
        for person_id, room_id in data.fixed:
            if room_id in self.rooms:
                self.assign(person_id, room_id)

    def count(self, person_id: int) -> int:
        return self._counts[person_id]

    def remaining(self, room_id: int) -> int:
        return max(0, self.rooms[room_id].needed - len(self._room_people[room_id]))

    def people_in_slot(self, slot_id: int) -> set[int]:
        return self._slot_people[slot_id]

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
        if person_id in self._slot_people[slot.id]:
            errors.append(MSG_SAME_SLOT)
        if self.is_unavailable(person_id, slot):
            errors.append(MSG_UNAVAILABLE)
        if self._counts[person_id] >= cap:
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


def allocate(data: EngineInput, seed: int) -> AllocationResult:
    state = AllocationState(data)
    assignments = []
    unfilled = []
    soft_violations = 0
    for room in data.rooms:
        missing = 0
        for _ in range(state.remaining(room.id)):
            candidates = [p for p in data.people if not state.hard_errors(p.id, room.id)]
            if not candidates:
                missing += 1
                continue
            best = min(candidates, key=lambda p: (state.count(p.id), p.id))
            soft_violations += len(state.soft_warnings(best.id, room.id))
            state.assign(best.id, room.id)
            assignments.append((best.id, room.id))
        if missing:
            unfilled.append(Unfilled(room.id, missing, "No eligible faculty"))
    stats = fairness_stats(state.count(p.id) for p in data.people)
    return AllocationResult(assignments, unfilled, stats, soft_violations, seed)
