"""Shared pytest fixtures for all apps."""

import pytest


@pytest.fixture
def dept(db):
    from faculty.models import Department

    return Department.objects.create(name="Computer Science")


@pytest.fixture
def make_faculty(db, dept):
    """Factory: make_faculty(name="A", **overrides) -> Faculty."""
    from faculty.models import Designation, Faculty

    counter = {"n": 0}

    def make(name=None, **extra):
        counter["n"] += 1
        n = counter["n"]
        defaults = {
            "employee_id": f"E{n:04d}",
            "name": name or f"Faculty {n:04d}",
            "email": f"f{n}@uni.edu",
            "department": dept,
            "designation": Designation.LECTURER,
        }
        defaults.update(extra)
        return Faculty.objects.create(**defaults)

    return make
