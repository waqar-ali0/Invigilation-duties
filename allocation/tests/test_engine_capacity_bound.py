from datetime import date, timedelta

from allocation.engine import EngineInput, Person, Room, Rules, Slot, capacity_check

D = date(2026, 12, 1)
RULES = Rules(max_per_day=2, avoid_back_to_back=False, exclude_course_teacher=False)


def test_capacity_limited_by_slots_a_person_can_attend():
    # 3 slots on one day, daily limit 2: nobody can do more than 2 even with a cap of 8.
    slots = [Slot(i, D, i) for i in range(3)]
    rooms = [Room(10 + i, i, 5) for i in range(3)]
    report = capacity_check(EngineInput(slots, rooms, [Person(1, 1, 8)], RULES))
    assert report.capacity == 2


def test_capacity_drops_for_unavailable_days():
    slots = [Slot(i, D + timedelta(days=i), 0) for i in range(4)]
    rooms = [Room(10 + i, i, 1) for i in range(4)]
    data = EngineInput(slots, rooms, [Person(1, 1, 8)], RULES, unavailable_days=frozenset({(1, D), (1, D + timedelta(days=1))}))
    assert capacity_check(data).capacity == 2


def test_capacity_counts_overlapping_slots_once():
    from datetime import time

    slots = [Slot(1, D, 0, time(9), time(12)), Slot(2, D, 1, time(10), time(13))]
    rooms = [Room(10, 1, 1), Room(11, 2, 1)]
    assert capacity_check(EngineInput(slots, rooms, [Person(1, 1, 8)], RULES)).capacity == 1
