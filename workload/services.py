"""Changing workload rules: confirmation, versions after publishing, and copying between periods."""

from django.db import transaction
from django.utils import timezone

from audit.log import log
from exams.models import PeriodStatus

from .models import CategoryRule, FacultyRule, WorkloadPolicy, WorkloadVersion


def snapshot(period):
    """Everything that decides workloads in `period`, as plain data."""
    policy = WorkloadPolicy.for_period(period)
    s = period.settings
    return {
        "default_max": s.max_duties_per_faculty,
        "max_per_day": s.max_duties_per_day,
        "policy": {
            "excused_absence_counts": policy.excused_absence_counts,
            "unexcused_absence_counts": policy.unexcused_absence_counts,
            "makeup_counts": policy.makeup_counts,
            "stand_in_counts": policy.stand_in_counts,
            "combined_periods": sorted(p.name for p in policy.combined_periods.all()),
        },
        "categories": [
            {"category": r.category.name, "target": r.target, "kind": r.kind, "min": r.min_duties, "max": r.max_duties, "per_course": r.per_course}
            for r in CategoryRule.objects.filter(period=period).select_related("category")
        ],
        "people": [
            {"faculty": f"{r.faculty.name} ({r.faculty.employee_id})", "target": r.target, "kind": r.kind, "min": r.min_duties,
             "max": r.max_duties, "exempt": r.exempt, "allow_inactive": r.allow_inactive, "reason": r.reason}
            for r in FacultyRule.objects.filter(period=period).select_related("faculty")
        ],
    }


def save_version(period, actor, reason):
    with transaction.atomic():
        last = WorkloadVersion.objects.filter(period=period).select_for_update().order_by("-number").first()
        version = WorkloadVersion.objects.create(
            period=period, number=(last.number + 1) if last else 1, created_by=actor, reason=reason, snapshot=snapshot(period)
        )
    return version


def rules_changed(period, actor, what):
    """Call after any rule change: confirmation lapses; after publishing, a new version is kept."""
    policy = WorkloadPolicy.for_period(period)
    if policy.confirmed_at is not None and period.is_draft:
        policy.confirmed_at, policy.confirmed_by = None, None
        policy.save(update_fields=["confirmed_at", "confirmed_by"])
    if not period.is_draft:
        save_version(period, actor, f"Changed after publishing: {what}")
    log(actor, "workload.change", period, after={"what": what})


def confirm(period, actor):
    policy = WorkloadPolicy.for_period(period)
    policy.confirmed_at, policy.confirmed_by = timezone.now(), actor
    policy.save(update_fields=["confirmed_at", "confirmed_by"])
    version = save_version(period, actor, "Confirmed")
    log(actor, "workload.confirm", period, after={"version": version.number})
    return version


def copy_rules(source, target, actor):
    """Copy defaults, designation rules, counting policy and personal rules (active faculty only)
    from `source` into `target`, replacing `target`'s. `source` is never changed."""
    with transaction.atomic():
        s, t = source.settings, target.settings
        t.max_duties_per_faculty, t.max_duties_per_day = s.max_duties_per_faculty, s.max_duties_per_day
        t.save(update_fields=["max_duties_per_faculty", "max_duties_per_day"])
        CategoryRule.objects.filter(period=target).delete()
        for r in CategoryRule.objects.filter(period=source):
            CategoryRule.objects.create(
                period=target, category_id=r.category_id, target=r.target, kind=r.kind,
                min_duties=r.min_duties, max_duties=r.max_duties, per_course=r.per_course,
            )
        FacultyRule.objects.filter(period=target).delete()
        for r in FacultyRule.objects.filter(period=source, faculty__is_active=True):
            FacultyRule.objects.create(
                period=target, faculty_id=r.faculty_id, target=r.target, kind=r.kind, min_duties=r.min_duties,
                max_duties=r.max_duties, exempt=r.exempt, allow_inactive=r.allow_inactive, reason=r.reason, updated_by=actor,
            )
        sp, tp = WorkloadPolicy.for_period(source), WorkloadPolicy.for_period(target)
        for name in ("excused_absence_counts", "unexcused_absence_counts", "makeup_counts", "stand_in_counts"):
            setattr(tp, name, getattr(sp, name))
        tp.save()
        rules_changed(target, actor, f"copied from {source.name}")
