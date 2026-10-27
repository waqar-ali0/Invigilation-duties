"""Backup pool pages: who is in the pool, and who is kept free in each time slot of a period."""

from collections import defaultdict

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Role
from accounts.permissions import role_required
from audit.log import log
from duties.backups import reserve_backups
from duties.models import BackupMember, BackupReservation, BackupRole, ReservationStatus
from exams.models import ExamPeriod, PeriodSettings, PeriodStatus
from faculty.models import Faculty, FacultyCategory

admin_only = role_required(Role.ADMIN)


class MemberForm(forms.ModelForm):
    class Meta:
        model = BackupMember
        fields = ["faculty", "role", "priority", "max_backup", "buildings", "notes"]
        widgets = {"buildings": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = BackupMember.objects.exclude(pk=self.instance.pk).values_list("faculty_id", flat=True)
        self.fields["faculty"].queryset = Faculty.objects.filter(is_active=True).exclude(pk__in=taken).select_related("department").order_by("name")
        if self.instance.pk:
            self.fields["faculty"].disabled = True


class BackupSettingsForm(forms.ModelForm):
    class Meta:
        model = PeriodSettings
        fields = ["backups_per_slot", "reserves_fill_shortages"]


@admin_only
def pool(request):
    editing = None
    if request.GET.get("edit", "").isdigit():
        editing = get_object_or_404(BackupMember, pk=request.GET["edit"])
    action = request.POST.get("action") if request.method == "POST" else None
    form = MemberForm(request.POST if action == "save" else None, instance=editing)
    if action == "save" and form.is_valid():
        member = form.save()
        log(request.user, "backup.member", member, after={"role": member.role, "priority": member.priority})
        messages.success(request, f"{member.faculty.name} is in the backup pool.")
        return redirect("backup_pool")
    if action == "remove":
        member = get_object_or_404(BackupMember, pk=request.POST.get("id") or 0)
        log(request.user, "backup.remove", member, before={"faculty": member.faculty_id})
        member.delete()
        messages.success(request, "Removed from the backup pool. Their past backup record is kept.")
        return redirect("backup_pool")
    if action == "add_category":
        category = get_object_or_404(FacultyCategory, pk=request.POST.get("category") or 0)
        role = request.POST.get("role") if request.POST.get("role") in BackupRole.values else BackupRole.ALSO_BACKUP
        added = 0
        for f in Faculty.objects.filter(category=category, is_active=True, backup__isnull=True):
            BackupMember.objects.create(faculty=f, role=role)
            added += 1
        log(request.user, "backup.add_category", category, after={"added": added, "role": role})
        messages.success(request, f"{added} {category.name} member(s) added to the backup pool.")
        return redirect("backup_pool")
    members = BackupMember.objects.select_related("faculty__department", "faculty__category").prefetch_related("buildings").annotate(
        reserved=Count("faculty__backup_reservations", filter=~Q(faculty__backup_reservations__status=ReservationStatus.RELEASED)),
        called=Count("faculty__backup_reservations", filter=Q(faculty__backup_reservations__status=ReservationStatus.CALLED)),
    )
    context = {
        "members": members,
        "form": form,
        "editing": editing,
        "categories": FacultyCategory.objects.filter(is_active=True),
        "roles": BackupRole.choices,
        "periods": ExamPeriod.objects.exclude(status=PeriodStatus.CLOSED),
    }
    return render(request, "portal/backup_pool.html", context)


@admin_only
def period_backups(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    settings_form = BackupSettingsForm(request.POST if request.POST.get("action") == "settings" else None, instance=period.settings)
    if request.method == "POST":
        if request.POST.get("action") == "settings" and settings_form.is_valid():
            settings_form.save()
            made = reserve_backups(period)
            log(request.user, "backup.settings", period, after={"per_slot": period.settings.backups_per_slot, "reserved": made})
            messages.success(request, f"Saved. {made} new reservation(s) made.")
            return redirect("period_backups", pk=pk)
        if request.POST.get("action") == "reserve":
            made = reserve_backups(period)
            messages.success(request, f"{made} new reservation(s) made.")
            return redirect("period_backups", pk=pk)
    by_slot = defaultdict(list)
    for r in BackupReservation.objects.filter(period=period).select_related("faculty", "slot", "duty__room_allocation"):
        by_slot[r.slot_id].append(r)
    rows = [(slot, by_slot.get(slot.id, [])) for slot in period.slots.order_by("date", "start_time")]
    context = {
        "period": period,
        "rows": rows,
        "settings_form": settings_form,
        "pool_size": BackupMember.objects.filter(faculty__is_active=True).count(),
    }
    return render(request, "portal/period_backups.html", context)
