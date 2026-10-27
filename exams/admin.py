from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.forms.models import BaseInlineFormSet

from .models import (
    Building,
    Room,
    ExamPeriod,
    ExamSession,
    PeriodSettings,
    PeriodStatus,
    RoomAllocation,
    TimeSlot,
)

LOCKED = (PeriodStatus.PUBLISHED, PeriodStatus.CLOSED)


class LockedAfterPublishMixin:
    """Once a period is published or closed, its schedule and rules are read-only here.

    `period_of(obj)` returns the exam period an object belongs to (None for a new object).
    """

    def period_of(self, obj):
        raise NotImplementedError

    def _locked(self, obj):
        period = self.period_of(obj) if obj is not None else None
        return period is not None and period.status in LOCKED

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if self._locked(obj):
            raise PermissionDenied("This exam period is published; its schedule can no longer be changed.")
        super().save_model(request, obj, form, change)

    def delete_queryset(self, request, queryset):
        if any(self._locked(obj) for obj in queryset):
            raise PermissionDenied("Some selected items belong to a published exam period.")
        super().delete_queryset(request, queryset)


class LockedInlineMixin:
    """Inline rows follow the lock of the parent object (an ExamPeriod or ExamSession)."""

    def _locked(self, parent):
        if parent is None:
            return False
        period = parent if isinstance(parent, ExamPeriod) else parent.slot.period
        return period.status in LOCKED

    def has_add_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)


class PeriodSettingsFormSet(BaseInlineFormSet):
    """A new period gets default settings as soon as it is saved, so the form updates those
    instead of trying to create a second set."""

    def save_new(self, form, commit=True):
        existing = PeriodSettings.objects.filter(period=self.instance).first()
        if existing is None:
            return super().save_new(form, commit=commit)
        for name, value in form.cleaned_data.items():
            if name in {f.name for f in PeriodSettings._meta.concrete_fields} and name not in ("id", "period"):
                setattr(existing, name, value)
        if commit:
            existing.save()
        return existing


class PeriodSettingsInline(LockedInlineMixin, admin.StackedInline):
    model = PeriodSettings
    formset = PeriodSettingsFormSet
    can_delete = False


@admin.register(ExamPeriod)
class ExamPeriodAdmin(LockedAfterPublishMixin, admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "status")
    list_filter = ("status",)
    readonly_fields = ("status", "published_at")
    inlines = [PeriodSettingsInline]

    def period_of(self, obj):
        return ExamPeriod.objects.get(pk=obj.pk) if obj.pk else None


@admin.register(TimeSlot)
class TimeSlotAdmin(LockedAfterPublishMixin, admin.ModelAdmin):
    list_display = ("__str__", "period")
    list_filter = ("period",)

    def period_of(self, obj):
        return obj.period if obj.period_id else None


class RoomAllocationInline(LockedInlineMixin, admin.TabularInline):
    model = RoomAllocation
    extra = 0


@admin.register(ExamSession)
class ExamSessionAdmin(LockedAfterPublishMixin, admin.ModelAdmin):
    list_display = ("course_code", "course_name", "department", "slot")
    list_filter = ("slot__period", "department")
    search_fields = ("course_code", "course_name")
    filter_horizontal = ("course_teachers",)
    inlines = [RoomAllocationInline]

    def period_of(self, obj):
        return obj.slot.period if obj.slot_id else None


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "is_active")
    search_fields = ("name", "campus")

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead, so past exams keep their building


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "building", "capacity", "invigilators_needed", "is_active")
    list_filter = ("building", "is_active")
    search_fields = ("name", "building__name")

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead, so past exams keep their room


@admin.register(RoomAllocation)
class RoomAllocationAdmin(LockedAfterPublishMixin, admin.ModelAdmin):
    list_display = ("room", "session", "student_count", "invigilators_required")
    list_filter = ("session__slot__period",)
    search_fields = ("room", "session__course_code")

    def period_of(self, obj):
        return obj.session.slot.period if obj.session_id else None
