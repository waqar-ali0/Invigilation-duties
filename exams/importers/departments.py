from django.db import transaction

from faculty.models import Department

from .reader import read_rows
from .result import ImportResult, is_blank, length_errors, missing_columns


def import_departments(file):
    """Columns: name. Departments that already exist are skipped."""
    rows = read_rows(file)
    if problem := missing_columns(rows, ["name"]):
        return problem

    existing = {n.lower() for n in Department.objects.values_list("name", flat=True)}
    seen, to_create, errors = set(), [], []
    for row in rows:
        n, name = row["_row"], str(row["name"]) if not is_blank(row["name"]) else ""
        if not name:
            errors.append(f"Row {n}: name is required")
        elif too_long := length_errors(n, {"name": name}, {"name": (Department, "name")}):
            errors.extend(too_long)
        elif name.lower() in seen:
            errors.append(f"Row {n}: duplicate department '{name}'")
        else:
            seen.add(name.lower())
            if name.lower() not in existing:
                to_create.append(Department(name=name))

    if errors:
        return ImportResult(errors=errors)
    with transaction.atomic():
        Department.objects.bulk_create(to_create)
    return ImportResult(created=len(to_create))
