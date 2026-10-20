from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from faculty.models import Department, Designation, Faculty
from faculty.signals import email_taken

from .reader import read_rows
from .result import ImportResult, is_blank, length_errors, missing_columns

COLUMNS = ["employee_id", "name", "email", "department", "designation"]
LIMITS = {
    "employee_id": (Faculty, "employee_id"),
    "name": (Faculty, "name"),
    "email": (Faculty, "email"),
}


def _designation(value):
    v = str(value).strip().lower()
    for choice in Designation:
        if v in (choice.value, choice.label.lower()):
            return choice.value
    return None


def import_faculty(file):
    """Columns: employee_id, name, email, department, designation.

    Existing faculty (matched by employee_id) are updated; others are created.
    """
    rows = read_rows(file)
    if problem := missing_columns(rows, COLUMNS):
        return problem

    departments = {d.name.lower(): d for d in Department.objects.all()}
    by_employee = {f.employee_id: f for f in Faculty.objects.all()}
    email_owner = {f.email.lower(): f.employee_id for f in by_employee.values()}

    errors, seen_ids, seen_emails, records = [], set(), set(), []
    for row in rows:
        n = row["_row"]
        values = {c: ("" if is_blank(row[c]) else str(row[c]).strip()) for c in COLUMNS}
        row_errors = [f"Row {n}: {c} is required" for c in COLUMNS if not values[c]]
        row_errors += length_errors(n, values, LIMITS)
        if row_errors:
            errors.extend(row_errors)
            continue
        emp, email = values["employee_id"], values["email"].lower()
        if emp in seen_ids:
            row_errors.append(f"Row {n}: duplicate employee_id '{emp}'")
        if email in seen_emails:
            row_errors.append(f"Row {n}: duplicate email '{values['email']}'")
        seen_ids.add(emp)
        seen_emails.add(email)
        try:
            validate_email(email)
        except ValidationError:
            row_errors.append(f"Row {n}: invalid email '{values['email']}'")
        else:
            existing = by_employee.get(emp)
            if email_owner.get(email, emp) != emp:
                row_errors.append(f"Row {n}: email '{values['email']}' belongs to another employee")
            elif existing is not None and existing.user_id and email_taken(email, existing.user_id):
                row_errors.append(f"Row {n}: email '{values['email']}' is already used by another account")
        dept = departments.get(values["department"].lower())
        if dept is None:
            row_errors.append(f"Row {n}: unknown department '{values['department']}'")
        designation = _designation(values["designation"])
        if designation is None:
            row_errors.append(f"Row {n}: unknown designation '{values['designation']}'")
        errors.extend(row_errors)
        if not row_errors:
            records.append((emp, values["name"], email, dept, designation))

    if errors:
        return ImportResult(errors=errors)

    result = ImportResult()
    with transaction.atomic():
        for emp, name, email, dept, designation in records:
            fields = {"name": name, "email": email, "department": dept, "designation": designation}
            if emp in by_employee:
                existing = by_employee[emp]
                for name, value in fields.items():
                    setattr(existing, name, value)
                existing.save(update_fields=list(fields))  # save() so linked accounts follow email changes
                result.updated += 1
            else:
                Faculty.objects.create(employee_id=emp, **fields)
                result.created += 1
    return result
