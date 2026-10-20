from datetime import date, timedelta

from allocation.engine import (
    CapacityReport,
    EngineInput,
    Person,
    Room,
    Rules,
    Slot,
    Unfilled,
    allocate,
    capacity_check,
)

D = date(2026, 12, 1)
OFF = Rules(max_per_day=2, avoid_back_to_back=False, exclude_course_teacher=False)
ON = Rules(max_per_day=2, avoid_back_to_back=True, exclude_course_teacher=True)


def people(n, cap=8, dept=1):
    return [Person(id=i, department_id=dept, cap=cap) for i in range(1, n + 1)]


def busy_elsewhere(person_id, n, first_room=900):
    """Slots, rooms and fixed duties that give `person_id` n duties on later days."""
    slots = [Slot(800 + i, D + timedelta(days=10 + i), 0) for i in range(n)]
    rooms = [Room(first_room + i, 800 + i, 1) for i in range(n)]
    fixed = tuple((person_id, first_room + i) for i in range(n))
    return slots, rooms, fixed


def test_same_seed_same_result():
    slots = [Slot(i, D + timedelta(days=i), 0) for i in range(4)]
    rooms = [Room(100 + i, i, 3) for i in range(4)]
    data = EngineInput(slots, rooms, people(10), OFF)
    assert allocate(data, 7) == allocate(data, 7)


def test_different_seed_can_differ():
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 1)], people(10), OFF)
    chosen = {allocate(data, seed).assignments[0][0] for seed in range(1, 21)}
    assert len(chosen) > 1


def test_hardest_slot_filled_first():
    # Slot 1 can only use persons 1-2; slot 2 can use anyone. Caps are 1.
    slots = [Slot(1, D, 0), Slot(2, D + timedelta(days=1), 0)]
    rooms = [Room(11, 2, 2), Room(10, 1, 2)]
    data = EngineInput(slots, rooms, people(4, cap=1), OFF, unavailable_slots=frozenset({(3, 1), (4, 1)}))
    res = allocate(data, seed=1)
    assert res.unfilled == []


def test_avoids_back_to_back_when_possible():
    extra_slots, extra_rooms, fixed = busy_elsewhere(2, 2)
    slots = [Slot(1, D, 0), Slot(2, D, 1)] + extra_slots
    rooms = [Room(10, 1, 1), Room(11, 2, 1)] + extra_rooms
    res = allocate(EngineInput(slots, rooms, people(2), ON, fixed=fixed), seed=1)
    assert (1, 10) in res.assignments and (2, 11) in res.assignments
    assert res.soft_violations == 0


def test_avoids_course_teacher_when_possible():
    extra_slots, extra_rooms, fixed = busy_elsewhere(2, 1)
    slots = [Slot(1, D, 0)] + extra_slots
    rooms = [Room(10, 1, 1, course_teacher_ids=frozenset({1}))] + extra_rooms
    res = allocate(EngineInput(slots, rooms, people(2), ON, fixed=fixed), seed=1)
    assert res.assignments == [(2, 10)]


def test_assigns_course_teacher_when_only_option_and_counts_violation():
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 1, course_teacher_ids=frozenset({1}))], people(1), ON)
    res = allocate(data, seed=1)
    assert res.assignments == [(1, 10)] and res.soft_violations == 1


def test_spreads_departments_within_room():
    staff = people(2, dept=1) + [Person(3, 2, 8), Person(4, 2, 8)]
    dept_of = {p.id: p.department_id for p in staff}
    for seed in range(1, 11):
        res = allocate(EngineInput([Slot(1, D, 0)], [Room(10, 1, 2)], staff, OFF), seed=seed)
        assert {dept_of[p] for p, _ in res.assignments} == {1, 2}


def test_unfilled_reason_period_cap():
    slots = [Slot(1, D, 0), Slot(2, D + timedelta(days=1), 0)]
    rooms = [Room(10, 1, 1), Room(11, 2, 1)]
    res = allocate(EngineInput(slots, rooms, people(1, cap=1), OFF), seed=1)
    assert res.unfilled == [Unfilled(11, 1, "All remaining faculty have reached their period cap")]


def test_unfilled_reason_unavailable():
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 1)], people(1), OFF, unavailable_days=frozenset({(1, D)}))
    assert allocate(data, seed=1).unfilled == [Unfilled(10, 1, "All remaining faculty are unavailable")]


def test_unfilled_reason_daily_limit():
    rules = Rules(max_per_day=1, avoid_back_to_back=False, exclude_course_teacher=False)
    data = EngineInput([Slot(1, D, 0), Slot(2, D, 1)], [Room(10, 1, 1), Room(11, 2, 1)], people(1), rules)
    assert allocate(data, seed=1).unfilled == [Unfilled(11, 1, "All remaining faculty have reached their daily limit")]


def test_unfilled_reason_all_in_slot():
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 1), Room(11, 1, 1)], people(1), OFF)
    assert allocate(data, seed=1).unfilled == [Unfilled(11, 1, "All faculty are already assigned in this time slot")]


def test_unfilled_reason_mixed():
    staff = [Person(1, 1, 8), Person(2, 1, 0)]
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 1)], staff, OFF, unavailable_days=frozenset({(1, D)}))
    assert allocate(data, seed=1).unfilled == [
        Unfilled(10, 1, "No eligible faculty: 1 unavailable, 1 at period cap, 0 at daily limit")
    ]


def test_capacity_check_flags_total_and_slot_shortage():
    data = EngineInput([Slot(1, D, 0)], [Room(10, 1, 3)], people(2, cap=1), OFF)
    assert capacity_check(data) == CapacityReport(seats_needed=3, capacity=2, ok=False, slot_shortages=[(1, 3, 2)])


def test_capacity_check_ok_and_counts_fixed():
    slots = [Slot(1, D, 0), Slot(2, D + timedelta(days=1), 0)]
    rooms = [Room(10, 1, 2), Room(11, 2, 2)]
    data = EngineInput(slots, rooms, people(2, cap=2), OFF, fixed=((1, 10),))
    assert capacity_check(data) == CapacityReport(seats_needed=3, capacity=3, ok=True, slot_shortages=[])
