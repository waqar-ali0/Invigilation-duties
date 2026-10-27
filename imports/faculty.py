"""Faculty import: any spreadsheet layout, matched by faculty ID, phone or email — never by name alone."""

import re
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.text import slugify

from audit.log import log
from faculty.models import Course, Department, EmploymentStatus, Faculty, FacultyCategory
from faculty.phones import normalize_phone
from faculty.signals import email_taken

from .base import Field, ImportKind, Option, Plan, RowPlan, Status, norm

CATEGORY_ALIASES = {
    "prof": "professor", "full professor": "professor", "professor": "professor",
    "assoc prof": "associate_professor", "associate prof": "associate_professor",
    "associate professor": "associate_professor",
    "asst prof": "assistant_professor", "assistant prof": "assistant_professor",
    "assistant professor": "assistant_professor",
    "lecturer": "lecturer",
    "lab engineer": "lab_engineer", "lab engr": "lab_engineer", "lab eng": "lab_engineer", "le": "lab_engineer",
    "visiting": "visiting", "visiting faculty": "visiting", "visiting lecturer": "visiting",
    "visiting professor": "visiting", "vf": "visiting",
    "backup": "backup", "backup faculty": "backup",
}
STATUS_ALIASES = {norm(v): v for v in EmploymentStatus.values} | {norm(label): v for v, label in EmploymentStatus.choices} | {
    "regular": EmploymentStatus.PERMANENT, "contractual": EmploymentStatus.CONTRACT, "leave": EmploymentStatus.ON_LEAVE,
}


def split_codes(text):
    return [c.strip().upper() for c in re.split(r"[;,/\n]+", text) if c.strip()]


class CategoryResolver:
    def __init__(self):
        self.by_name = {}
        for c in FacultyCategory.objects.all():
            self.by_name[norm(c.name)] = c
            self.by_name[norm(c.code)] = c

    def __call__(self, text):
        key = norm(text)
        if key in self.by_name:
            return self.by_name[key]
        code = CATEGORY_ALIASES.get(key)
        return self.by_name.get(norm(code)) if code else None


class FacultyImport(ImportKind):
    key = "faculty"
    title = "Faculty"
    description = (
        "Your own spreadsheet works as it is: pick which column holds what. Faculty IDs are made "
        "automatically (FAC-0001…) for new people. Existing faculty are found by faculty ID, phone "
        "number or email; someone with only the same name is shown for you to decide."
    )
    fields = (
        Field("faculty_id", "Faculty ID", synonyms=("employee id", "emp id", "staff id", "id", "emp no", "employee no", "employee number", "personnel no")),
        Field("name", "Name", required=True, synonyms=("faculty name", "teacher name", "full name", "faculty", "teacher", "name of faculty", "name of teacher")),
        Field("category", "Designation", required=True, synonyms=("category", "rank", "post", "position", "title", "faculty category")),
        Field("department", "Department", synonyms=("dept", "department name", "dept name", "school")),
        Field("phone", "Phone", synonyms=("phone number", "mobile", "mobile number", "mobile no", "contact", "contact number", "contact no", "cell", "cell no", "cell number", "whatsapp", "whatsapp number", "ph no", "phone no")),
        Field("email", "Email", synonyms=("e mail", "email address", "mail", "email id")),
        Field("employment_status", "Employment status", synonyms=("status", "job type", "employment", "job status")),
        Field("courses", "Courses taught", synonyms=("courses", "course codes", "subjects", "course")),
        Field("remarks", "Remarks", synonyms=("notes", "comments", "remark")),
    )

    @property
    def options(self):
        departments = tuple((str(d.pk), d.name) for d in Department.objects.all())
        return (
            Option("default_department", "Department for rows without one", "select", (("", "— none —"),) + departments,
                   default="", help="Used for new faculty when the file has no department or the cell is blank."),
            Option("add_departments", "Add departments that are not in the system yet", default=False),
            Option("add_categories", "Add designations that are not in the system yet", default=False),
        )

    # ------------------------------------------------------------------ plan
    def plan(self, batch):
        o = batch.options
        resolve_category = CategoryResolver()
        departments = {norm(d.name): d for d in Department.objects.all()}
        default_dept = Department.objects.filter(pk=o.get("default_department") or None).first()
        everyone = list(Faculty.objects.select_related("department", "category").prefetch_related("courses"))
        by_id = {f.employee_id.lower(): f for f in everyone}
        by_phone = {f.phone_e164: f for f in everyone if f.phone_e164}
        by_email = {f.email.lower(): f for f in everyone if f.email}
        by_name = defaultdict(list)
        for f in everyone:
            by_name[norm(f.name)].append(f)
        seen = {"faculty ID": {}, "phone number": {}, "email": {}}
        new_categories, new_departments = {}, {}
        rows = []

        for number, cells in batch.rows:
            v = self.values(batch, cells)
            p = RowPlan(number, v, label=v["name"] or f"Row {number}")
            p.parsed = {}
            if not v["name"]:
                p.errors.append("Name is missing")
            elif len(v["name"]) > 150:
                p.errors.append("Name is longer than 150 characters")
            if len(v["faculty_id"]) > 50:
                p.errors.append("Faculty ID is longer than 50 characters")

            category = None
            if not v["category"]:
                p.errors.append("Designation is missing")
            else:
                category = resolve_category(v["category"])
                if category is None:
                    if o.get("add_categories"):
                        new_categories.setdefault(norm(v["category"]), v["category"].strip())
                        p.warnings.append(f"New designation '{v['category']}' will be added")
                    else:
                        p.errors.append(f"Unknown designation '{v['category']}'")
                elif not category.is_active:
                    p.errors.append(f"Designation '{category.name}' is no longer in use")
            p.parsed["category"] = category or (norm(v["category"]) if v["category"] else None)

            dept = None
            if v["department"]:
                dept = departments.get(norm(v["department"]))
                if dept is None:
                    if o.get("add_departments"):
                        new_departments.setdefault(norm(v["department"]), v["department"].strip())
                        p.warnings.append(f"New department '{v['department']}' will be added")
                        dept = norm(v["department"])
                    else:
                        p.errors.append(f"Unknown department '{v['department']}'")
            p.parsed["department"] = dept

            email = v["email"].lower()
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    p.errors.append(f"Email '{v['email']}' is not valid")
                    email = ""
            p.parsed["email"] = email

            phone = normalize_phone(v["phone"]) if v["phone"] else None
            if not v["phone"]:
                p.warnings.append("No phone number")
            elif phone is None:
                p.warnings.append(f"Phone number '{v['phone']}' is not valid")
            elif len(v["phone"]) > 40:
                p.errors.append("Phone number is longer than 40 characters")
            p.parsed["phone"] = phone

            status = None
            if v["employment_status"]:
                status = STATUS_ALIASES.get(norm(v["employment_status"]))
                if status is None:
                    p.warnings.append(f"Employment status '{v['employment_status']}' not recognised; left unchanged")
            p.parsed["employment_status"] = status
            p.parsed["courses"] = split_codes(v["courses"]) if v["courses"] else []

            for label, value in (("faculty ID", v["faculty_id"].lower()), ("phone number", phone), ("email", email)):
                if value:
                    if value in seen[label]:
                        p.errors.append(f"Same {label} as row {seen[label][value]}")
                    else:
                        seen[label][value] = number

            found = {}
            for label, f in (
                ("faculty ID", by_id.get(v["faculty_id"].lower()) if v["faculty_id"] else None),
                ("phone", by_phone.get(phone) if phone else None),
                ("email", by_email.get(email) if email else None),
            ):
                if f is not None:
                    found.setdefault(f.pk, (f, []))[1].append(label)

            if len(found) > 1:
                p.status = Status.REVIEW
                p.candidates = [f for f, _ in found.values()]
                p.warnings.append(
                    "Matches different faculty: "
                    + "; ".join(f"{f.name} ({f.employee_id}) by {', '.join(why)}" for f, why in found.values())
                )
            elif found:
                (f, why), = found.values()
                p.match = f
                p.warnings.append(f"Matches {f.name} ({f.employee_id}) by {', '.join(why)}")
                if f.user_id and email and email != (f.email or "").lower() and email_taken(email, f.user_id):
                    p.errors.append(f"Email '{v['email']}' is already used by another account")
                p.changes = self._changes(f, v, p.parsed)
                p.status = Status.UPDATE if p.changes else Status.SAME
            else:
                namesakes = by_name.get(norm(v["name"]), []) if v["name"] else []
                if namesakes:
                    p.status = Status.REVIEW
                    p.candidates = namesakes
                    p.warnings.append("Someone with the same name already exists; a name alone is not proof it is the same person")
                else:
                    p.status = Status.NEW
                if dept is None:
                    if default_dept is not None:
                        p.parsed["department"] = default_dept
                    else:
                        p.errors.append("Department is missing (choose a department for rows without one)")
            rows.append(p.finish())

        notes = []
        if new_categories:
            notes.append("Designations that will be added: " + ", ".join(sorted(new_categories.values())))
        if new_departments:
            notes.append("Departments that will be added: " + ", ".join(sorted(new_departments.values())))
        plan = Plan(rows, notes)
        plan.new_categories, plan.new_departments = new_categories, new_departments
        return plan

    def _changes(self, f, v, parsed):
        """(field, old, new) for what this row would change. Blank cells never erase anything."""
        changes = []
        if v["name"] and v["name"] != f.name:
            changes.append(("Name", f.name, v["name"]))
        category = parsed["category"]
        if isinstance(category, FacultyCategory) and category.pk != f.category_id:
            changes.append(("Designation", f.category.name, category.name))
        elif isinstance(category, str) and category:
            changes.append(("Designation", f.category.name, v["category"]))
        dept = parsed["department"]
        if isinstance(dept, Department) and dept.pk != f.department_id:
            changes.append(("Department", f.department.name, dept.name))
        elif isinstance(dept, str):
            changes.append(("Department", f.department.name, v["department"]))
        if parsed["email"] and parsed["email"] != (f.email or "").lower():
            changes.append(("Email", f.email or "—", parsed["email"]))
        if parsed["phone"] and parsed["phone"] != f.phone_e164:
            changes.append(("Phone", f.phone or "—", v["phone"]))
        if parsed["employment_status"] and parsed["employment_status"] != f.employment_status:
            changes.append(("Employment status", f.get_employment_status_display(), EmploymentStatus(parsed["employment_status"]).label))
        if v["remarks"] and v["remarks"] != f.remarks:
            changes.append(("Remarks", f.remarks or "—", v["remarks"]))
        have = {c.code.upper() for c in f.courses.all()}
        added = [c for c in parsed["courses"] if c not in have]
        if added:
            changes.append(("Courses", ", ".join(sorted(have)) or "—", "adds " + ", ".join(added)))
        return changes

    # ------------------------------------------------------------------ commit
    def commit(self, batch, selected, decisions, mode, actor):
        counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "invalid": 0}
        created_ids = []
        with transaction.atomic():
            plan = self.plan(batch)
            categories, departments = {}, {}

            def category_of(p):
                c = p.parsed["category"]
                if isinstance(c, FacultyCategory):
                    return c
                if c not in categories:
                    name = plan.new_categories[c]
                    code = slugify(name).replace("-", "_")[:40] or "category"
                    base, n = code, 2
                    while FacultyCategory.objects.filter(code=code).exists():
                        code, n = f"{base[:36]}_{n}", n + 1
                    categories[c], _ = FacultyCategory.objects.get_or_create(name=name, defaults={"code": code, "order": 50})
                return categories[c]

            def department_of(p):
                d = p.parsed["department"]
                if d is None or isinstance(d, Department):
                    return d
                if d not in departments:
                    departments[d], _ = Department.objects.get_or_create(name=plan.new_departments[d])
                return departments[d]

            for p in plan.rows:
                if p.status == Status.INVALID:
                    counts["invalid"] += 1
                    continue
                if p.number not in selected:
                    counts["skipped"] += 1
                    continue
                if p.status == Status.SAME:
                    counts["unchanged"] += 1
                    continue
                target, create = p.match, p.status == Status.NEW
                if p.status == Status.REVIEW:
                    decision = decisions.get(p.number, "skip")
                    if decision == "new":
                        create = True
                    else:
                        target = next((c for c in p.candidates if str(c.pk) == decision), None)
                        if target is None:
                            counts["skipped"] += 1
                            continue
                        p.changes = self._changes(target, p.values, p.parsed)
                if create:
                    f = self._create(p, category_of(p), department_of(p))
                    created_ids.append(f.employee_id)
                    counts["created"] += 1
                elif mode == "new_only":
                    counts["skipped"] += 1
                elif p.changes:
                    self._update(target, p, category_of, department_of)
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
            result = {**counts, "created_ids": created_ids[:200]}
            log(actor, "import.faculty", batch, after={"file": batch.filename, **counts})
        return result

    def _create(self, p, category, department):
        v, parsed = p.values, p.parsed
        f = Faculty(
            employee_id=v["faculty_id"],
            name=v["name"],
            email=parsed["email"] or None,
            phone=v["phone"],
            department=department,
            category=category,
            employment_status=parsed["employment_status"] or (EmploymentStatus.VISITING if category.is_visiting else EmploymentStatus.PERMANENT),
            remarks=v["remarks"],
        )
        f.save()
        self._add_courses(f, parsed["courses"], department)
        return f

    def _update(self, f, p, category_of, department_of):
        v, parsed = p.values, p.parsed
        if v["name"]:
            f.name = v["name"]
        if parsed["category"]:
            f.category = category_of(p)
        if parsed["department"]:
            f.department = department_of(p)
        if parsed["email"]:
            f.email = parsed["email"]
        if parsed["phone"]:
            f.phone = v["phone"]
        if parsed["employment_status"]:
            f.employment_status = parsed["employment_status"]
        if v["remarks"]:
            f.remarks = v["remarks"]
        f.save()  # save() so linked accounts follow email changes
        self._add_courses(f, parsed["courses"], f.department)

    def _add_courses(self, f, codes, department):
        for code in codes:
            course, _ = Course.objects.get_or_create(code=code[:30], defaults={"name": code, "department": department})
            f.courses.add(course)
