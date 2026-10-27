"""Work out each person's workload rule for a period: their own rule over their category's rule
over the period's default maximum. Nothing is invented: an unset target stays None."""

from collections import defaultdict
from dataclasses import dataclass, field

from faculty.models import Faculty

from .models import CategoryRule, FacultyRule, TargetKind


@dataclass
class Rule:
    target: int | None = None
    kind: str = ""
    minimum: int | None = None
    maximum: int | None = None
    exempt: bool = False
    allow_inactive: bool = False
    sources: list = field(default_factory=list)  # e.g. ["Lecturer rule", "own rule"]
    note: str = ""  # e.g. "2 per course × 3 courses"

    @property
    def has_target(self):
        return self.target is not None

    @property
    def kind_label(self):
        return TargetKind(self.kind).label if self.kind else ""

    def describe(self):
        if self.exempt:
            return "Exempt this period"
        parts = []
        if self.target is not None:
            parts.append(f"{self.kind_label or 'About'} {self.target}")
        else:
            parts.append("Target not set")
        if self.minimum is not None and self.kind not in (TargetKind.EXACT, TargetKind.MINIMUM):
            parts.append(f"min {self.minimum}")
        if self.maximum is not None and self.kind not in (TargetKind.EXACT, TargetKind.MAXIMUM):
            parts.append(f"max {self.maximum}")
        return " · ".join(parts)

    def bounds(self):
        """(minimum, maximum) after applying the target's kind; None means no bound."""
        lo, hi = self.minimum, self.maximum
        if self.target is not None:
            if self.kind == TargetKind.EXACT:
                lo, hi = self.target, self.target
            elif self.kind == TargetKind.MINIMUM:
                lo = max(lo or 0, self.target)
            elif self.kind == TargetKind.MAXIMUM:
                hi = self.target if hi is None else min(hi, self.target)
        return lo, hi


def courses_taught(period):
    """{faculty id: number of courses they teach that have an exam in `period`}."""
    from exams.models import ExamSession

    sessions = ExamSession.objects.filter(slot__period=period)
    codes_in_period = {c.upper() for c in sessions.values_list("course_code", flat=True)}
    taught = defaultdict(set)
    for faculty_id, code in sessions.values_list("course_teachers", "course_code"):
        if faculty_id:
            taught[faculty_id].add(code.upper())
    for faculty_id, code in Faculty.courses.through.objects.values_list("faculty_id", "course__code"):
        if code.upper() in codes_in_period:
            taught[faculty_id].add(code.upper())
    return {fid: len(codes) for fid, codes in taught.items()}


def resolve_rules(period, faculty=None):
    """{faculty id: Rule} for `faculty` (default: everyone) in `period`."""
    people = list(faculty if faculty is not None else Faculty.objects.select_related("category"))
    default_max = period.settings.max_duties_per_faculty
    by_category = {r.category_id: r for r in CategoryRule.objects.filter(period=period)}
    by_person = {r.faculty_id: r for r in FacultyRule.objects.filter(period=period)}
    course_counts = None
    rules = {}
    for f in people:
        rule = Rule(maximum=default_max)
        if default_max is not None:
            rule.sources.append("period default")
        cr = by_category.get(f.category_id)
        if cr is not None:
            rule.sources.append(f"{f.category.name} rule")
            if cr.per_course:
                if course_counts is None:
                    course_counts = courses_taught(period)
                n = course_counts.get(f.id, 0)
                rule.target = cr.per_course * n
                rule.note = f"{cr.per_course} per course × {n} course{'s' if n != 1 else ''}"
            elif cr.target is not None:
                rule.target = cr.target
            rule.kind = cr.kind or (TargetKind.PREFERRED if rule.target is not None else "")
            if cr.min_duties is not None:
                rule.minimum = cr.min_duties
            if cr.max_duties is not None:
                rule.maximum = cr.max_duties
        fr = by_person.get(f.id)
        if fr is not None:
            rule.sources.append("own rule")
            rule.exempt = fr.exempt
            rule.allow_inactive = fr.allow_inactive
            if fr.target is not None:
                rule.target, rule.note = fr.target, fr.reason
                rule.kind = fr.kind or TargetKind.PREFERRED
            elif fr.kind:
                rule.kind = fr.kind
            if fr.min_duties is not None:
                rule.minimum = fr.min_duties
            if fr.max_duties is not None:
                rule.maximum = fr.max_duties
        rules[f.id] = rule
    return rules
