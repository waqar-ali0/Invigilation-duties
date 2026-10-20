from collections import Counter
from datetime import date, timedelta

from allocation.engine import (
    AllocationState,
    EngineInput,
    FairnessStats,
    Person,
    Room,
    Rules,
    Slot,
    allocate,
    fairness_stats,
)

D = date(2026, 12, 1)
RULES = Rules(max_per_day=2, avoid_back_to_back=False, exclude_course_teacher=False)


def people(n, cap=8):
    return [Person(id=i, department_id=1, cap=cap) for i in range(1, n + 1)]


def one_slot_per_day(days, seats):
    slots = [Slot(i, D + timedelta(days=i), 0) for i in range(days)]
    rooms = [Room(100 + i, i, seats) for i in range(days)]
    return slots, rooms


def test_no_person_twice_in_same_slot():
    data = EngineInput(slots=[Slot(1, D, 0)], rooms=[Room(10, 1, 2), Room(11, 1, 2)], people=people(4), rules=RULES)
    res = allocate(data, seed=1)
    assert len(res.assignments) == 4 and len({p for p, _ in res.assignments}) == 4


def test_period_cap_respected_and_shortage_reported():
    slots, rooms = one_slot_per_day(5, 2)
    res = allocate(EngineInput(slots, rooms, people(2, cap=3), RULES), seed=1)
    counts = Counter(p for p, _ in res.assignments)
    assert max(counts.values()) == 3
    assert sum(u.count for u in res.unfilled) == 4


def test_daily_limit_respected():
    slots = [Slot(1, D, 0), Slot(2, D, 1), Slot(3, D, 2)]
    rooms = [Room(10, 1, 1), Room(11, 2, 1), Room(12, 3, 1)]
    res = allocate(EngineInput(slots, rooms, people(1), RULES), seed=1)
    assert len(res.assignments) == 2
    assert sum(u.count for u in res.unfilled) == 1


def test_unavailable_day_and_slot_honoured():
    slots = [Slot(1, D, 0), Slot(2, D + timedelta(days=1), 0)]
    rooms = [Room(10, 1, 1), Room(11, 2, 1)]
    data = EngineInput(
        slots,
        rooms,
        people(2),
        RULES,
        unavailable_days=frozenset({(1, D)}),
        unavailable_slots=frozenset({(2, 2)}),
    )
    res = allocate(data, seed=1)
    assert sorted(res.assignments) == [(1, 11), (2, 10)]


def test_fixed_duties_count_toward_caps_and_are_not_returned():
    slots, rooms = one_slot_per_day(3, 1)
    data = EngineInput(slots, rooms, people(2, cap=1), RULES, fixed=((1, 100),))
    res = allocate(data, seed=1)
    assert res.assignments == [(2, 101)]
    assert sum(u.count for u in res.unfilled) == 1


def test_even_spread_when_unconstrained():
    slots, rooms = one_slot_per_day(5, 4)
    res = allocate(EngineInput(slots, rooms, people(10), RULES), seed=1)
    assert res.stats == FairnessStats(min=2, max=2, avg=2.0)


def test_check_reports_hard_errors():
    slots = [Slot(1, D, 0)]
    rooms = [Room(10, 1, 2), Room(11, 1, 2)]
    state = AllocationState(EngineInput(slots, rooms, people(1, cap=1), RULES))
    state.assign(1, 10)
    errors, _ = state.check(1, 11)
    assert "Already assigned in this time slot" in errors
    assert "Reached period cap (1)" in errors


def test_check_reports_soft_warnings_when_rules_on():
    rules = Rules(max_per_day=2, avoid_back_to_back=True, exclude_course_teacher=True)
    slots = [Slot(1, D, 0), Slot(2, D, 1)]
    rooms = [Room(10, 1, 1), Room(11, 2, 1, course_teacher_ids=frozenset({1}))]
    state = AllocationState(EngineInput(slots, rooms, people(1), rules))
    state.assign(1, 10)
    assert state.check(1, 11) == ([], ["Back-to-back with another duty", "Teaches this course"])


def test_check_ignores_soft_rules_when_off():
    slots = [Slot(1, D, 0), Slot(2, D, 1)]
    rooms = [Room(10, 1, 1), Room(11, 2, 1, course_teacher_ids=frozenset({1}))]
    state = AllocationState(EngineInput(slots, rooms, people(1), RULES))
    state.assign(1, 10)
    assert state.check(1, 11) == ([], [])


def test_fairness_stats_empty():
    assert fairness_stats([]) == FairnessStats(0, 0, 0.0)


def test_fairness_stats_rounds_average():
    assert fairness_stats([1, 1, 2]) == FairnessStats(1, 2, 1.33)
