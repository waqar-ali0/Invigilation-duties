import pytest


@pytest.fixture
def small_period(period, make_room, make_faculty):
    """2 slots, 3 rooms (2 seats each), 8 eligible faculty — plenty of capacity."""
    make_room(day=1, course="CS101", room="A1")
    make_room(day=1, course="CS102", room="A2")
    make_room(day=2, course="CS201", room="B1")
    for i in range(8):
        make_faculty(f"Teacher {i}")
    return period


@pytest.fixture
def short_period(period, make_room, make_faculty):
    """One room needing 5 invigilators but only 2 faculty."""
    make_room(day=1, room="Hall", students=150, needed=5)
    make_faculty("Only One")
    make_faculty("Only Two")
    return period
