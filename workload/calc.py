"""Workload figures per person and period. Dashboards, reports and the engine all use these, so
they always agree. An assigned duty is never treated as completed."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from duties.models import Duty, DutySource, DutyStatus
from faculty.models import Faculty

from .models import TargetKind, WorkloadPolicy
from .rules import Rule, resolve_rules


def counts_toward_target(duty, policy):
    """Does this duty count toward its holder's target under `policy`?"""
    if duty.status == DutyStatus.CANCELLED:
        return False
    if duty.status == DutyStatus.ABSENT:
        excused = getattr(duty, "absence_excused", None)
        return policy.excused_absence_counts if excused else policy.unexcused_absence_counts
    if duty.source == DutySource.STAND_IN and not policy.stand_in_counts:
        return False
    if duty.source == "makeup" and not policy.makeup_counts:
        return False
    return True


def prior_counts(period):
    """{faculty id: duties counted from the periods whose duties count together with `period`}."""
    policy = WorkloadPolicy.for_period(period)
    others = list(policy.combined_periods.exclude(pk=period.pk))
    totals = Counter()
    if others:
        for d in Duty.objects.filter(period__in=others).only("faculty_id", "status", "source"):
            if counts_toward_target(d, policy):
                totals[d.faculty_id] += 1
    return totals


class Flag:
    EXEMPT = "exempt"
    NO_TARGET = "no_target"
    BELOW = "below"
    AT = "at"
    NEAR_LIMIT = "near_limit"
    OVER = "over"
    NOT_ELIGIBLE = "not_eligible"

    LABELS = {
        EXEMPT: ("Exempt", ""),
        NO_TARGET: ("No target set", "warn"),
        BELOW: ("Below target", "info"),
        AT: ("At target", "ok"),
        NEAR_LIMIT: ("Near limit", "warn"),
        OVER: ("Over limit", "danger"),
        NOT_ELIGIBLE: ("Not eligible", ""),
    }


@dataclass
class Load:
    faculty: Faculty
    rule: Rule
    assigned: int = 0  # duties held in this period (not handed over)
    completed: int = 0
    pending: int = 0  # assigned, attendance not marked yet
    missed: int = 0
    missed_excused: int = 0
    cancelled: int = 0  # handed over to someone else (swap / replacement)
    stand_ins: int = 0
    makeup_owed: int = 0
    makeup_done: int = 0
    prior: int = 0  # from periods counted together with this one
    counted: int = 0  # what counts toward the target under the period's policy
    history: int = 0  # completed duties in all earlier periods
    flags: list = field(default_factory=list)

    @property
    def remaining(self):
        if self.rule.target is None or self.rule.exempt:
            return None
        return max(0, self.rule.target - self.counted)

    @property
    def percent(self):
        if not self.rule.target:
            return None
        return round(100 * self.counted / self.rule.target)

    @property
    def flag(self):
        return self.flags[0] if self.flags else ""

    @property
    def flag_labels(self):
        return [Flag.LABELS[f] for f in self.flags]


def period_loads(period, faculty=None):
    """[Load] for everyone eligible in `period` plus anyone holding duties there, by name."""
    policy = WorkloadPolicy.for_period(period)
    duties = list(Duty.objects.filter(period=period).only("faculty_id", "status", "source", *_absence_fields()))
    holders = {d.faculty_id for d in duties}
    people = Faculty.objects.select_related("department", "category")
    if faculty is not None:
        people = people.filter(pk__in=[f.pk for f in faculty])
    people = [f for f in people if f.pk in holders or (f.is_active and not f.is_exempt)]
    rules = resolve_rules(period, people)
    prior = prior_counts(period)
    history = Counter(
        Duty.objects.filter(status=DutyStatus.COMPLETED, period__start_date__lt=period.start_date).values_list("faculty_id", flat=True)
    )
    owed, done = _makeups(period)
    by_person = defaultdict(list)
    for d in duties:
        by_person[d.faculty_id].append(d)
    loads = []
    for f in people:
        rule = rules[f.pk]
        load = Load(f, rule, prior=prior.get(f.pk, 0), history=history.get(f.pk, 0),
                    makeup_owed=owed.get(f.pk, 0), makeup_done=done.get(f.pk, 0))
        for d in by_person[f.pk]:
            if d.status == DutyStatus.CANCELLED:
                load.cancelled += 1
                continue
            load.assigned += 1
            if d.status == DutyStatus.COMPLETED:
                load.completed += 1
                if d.source == DutySource.STAND_IN:
                    load.stand_ins += 1
            elif d.status == DutyStatus.ASSIGNED:
                load.pending += 1
            elif d.status == DutyStatus.ABSENT:
                load.missed += 1
                if getattr(d, "absence_excused", None):
                    load.missed_excused += 1
            if counts_toward_target(d, policy):
                load.counted += 1
        load.counted += load.prior
        load.flags = _flags(f, load)
        loads.append(load)
    loads.sort(key=lambda x: x.faculty.name)
    return loads


def _absence_fields():
    return ["absence_excused"] if any(f.name == "absence_excused" for f in Duty._meta.fields) else []


def _makeups(period):
    """({faculty id: make-up duties still owed}, {faculty id: make-ups done}) — see stage D."""
    try:
        from duties.models import Obligation
    except ImportError:
        return {}, {}
    owed, done = Counter(), Counter()
    for o in Obligation.objects.filter(original__period=period).select_related("makeup"):
        if o.status == "completed":
            done[o.owed_by_id] += 1
        elif o.is_outstanding:
            owed[o.owed_by_id] += 1
    return owed, done


def _flags(f, load):
    rule = load.rule
    if rule.exempt:
        return [Flag.EXEMPT]
    if not (f.is_active and not f.is_exempt) and not rule.allow_inactive:
        return [Flag.NOT_ELIGIBLE] + ([Flag.OVER] if load.assigned else [])
    flags = []
    low, high = rule.bounds()
    if high is not None and load.counted > high:
        flags.append(Flag.OVER)
    elif high is not None and load.counted >= high - 1 and load.counted >= 1 and (rule.target is None or load.counted >= rule.target):
        flags.append(Flag.NEAR_LIMIT)
    if rule.target is None:
        flags.append(Flag.NO_TARGET)
    elif load.counted < rule.target or (low is not None and load.counted < low):
        flags.append(Flag.BELOW)
    elif load.counted == rule.target or rule.kind in (TargetKind.MINIMUM, TargetKind.PREFERRED):
        flags.append(Flag.AT)
    if Flag.OVER in flags:
        flags = [Flag.OVER] + [x for x in flags if x != Flag.OVER]
    return flags


def summary(loads):
    """Counts per flag for dashboards."""
    c = Counter()
    for load in loads:
        for f in load.flags:
            c[f] += 1
    return c
