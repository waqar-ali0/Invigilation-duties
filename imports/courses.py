"""Course import, including who teaches each course (used for visiting faculty workloads)."""

import re

from django.db import transaction

from audit.log import log
from faculty.models import Course, Department, Faculty
from faculty.phones import normalize_phone

from .base import Field, ImportKind, Plan, RowPlan, Status, norm


class CourseImport(ImportKind):
    key = "courses"
    title = "Courses"
    description = (
        "Courses and their teachers. Teachers are given by faculty ID, email or phone number "
        "(separate several with ;). Teachers are only ever added to a course, never removed."
    )
    fields = (
        Field("code", "Course code", required=True, synonyms=("code", "course", "course no", "subject code")),
        Field("name", "Course name", required=True, synonyms=("name", "title", "course title", "subject", "subject name")),
        Field("department", "Department", synonyms=("dept",)),
        Field("teachers", "Teachers", synonyms=("teacher", "faculty", "instructor", "instructors", "taught by", "faculty id")),
    )

    def plan(self, batch):
        departments = {norm(d.name): d for d in Department.objects.all()}
        courses = {c.code.upper(): c for c in Course.objects.select_related("department").prefetch_related("teachers")}
        everyone = list(Faculty.objects.all())
        lookup = {}
        for f in everyone:
            lookup[f.employee_id.lower()] = f
            if f.email:
                lookup[f.email.lower()] = f
            if f.phone_e164:
                lookup[f.phone_e164] = f
        seen, rows = {}, []
        for number, cells in batch.rows:
            v = self.values(batch, cells)
            code = v["code"].upper()
            p = RowPlan(number, v, label=f"{code} — {v['name']}" if code else f"Row {number}")
            p.parsed = {"teachers": []}
            if not code:
                p.errors.append("Course code is missing")
            elif len(code) > 30:
                p.errors.append("Course code is longer than 30 characters")
            elif code in seen:
                p.errors.append(f"Same course as row {seen[code]}")
            else:
                seen[code] = number
            if not v["name"]:
                p.errors.append("Course name is missing")
            dept = None
            if v["department"]:
                dept = departments.get(norm(v["department"]))
                if dept is None:
                    p.errors.append(f"Unknown department '{v['department']}'")
            p.parsed["department"] = dept
            for token in [t.strip() for t in re.split(r"[;,\n]+", v["teachers"]) if t.strip()]:
                f = lookup.get(token.lower()) or lookup.get(normalize_phone(token) or "")
                if f is None:
                    p.errors.append(f"Teacher '{token}' not found (use faculty ID, email or phone)")
                else:
                    p.parsed["teachers"].append(f)
            existing = courses.get(code)
            if existing:
                p.match = existing
                if v["name"] and v["name"] != existing.name:
                    p.changes.append(("Course name", existing.name, v["name"]))
                if dept and dept.pk != existing.department_id:
                    p.changes.append(("Department", existing.department or "—", dept.name))
                have = {t.pk for t in existing.teachers.all()}
                added = [f.name for f in p.parsed["teachers"] if f.pk not in have]
                if added:
                    p.changes.append(("Teachers", "", "adds " + ", ".join(added)))
                p.status = Status.UPDATE if p.changes else Status.SAME
            rows.append(p.finish())
        return Plan(rows)

    def commit(self, batch, selected, decisions, mode, actor):
        counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "invalid": 0}
        with transaction.atomic():
            for p in self.plan(batch).rows:
                if p.status == Status.INVALID:
                    counts["invalid"] += 1
                    continue
                if p.number not in selected or (p.status == Status.UPDATE and mode == "new_only"):
                    counts["skipped"] += 1
                    continue
                if p.status == Status.SAME:
                    counts["unchanged"] += 1
                    continue
                course = p.match or Course(code=p.values["code"].upper())
                course.name = p.values["name"]
                if p.parsed["department"]:
                    course.department = p.parsed["department"]
                course.save()
                course.teachers.add(*p.parsed["teachers"])
                counts["updated" if p.match else "created"] += 1
            log(actor, "import.courses", batch, after={"file": batch.filename, **counts})
        return counts
