from django.contrib import admin

from .models import DesignationCap, ExamPeriod, ExamSession, PeriodSettings, RoomAllocation, TimeSlot


class PeriodSettingsInline(admin.StackedInline):
    model = PeriodSettings
    can_delete = False


class DesignationCapInline(admin.TabularInline):
    model = DesignationCap
    extra = 0


@admin.register(ExamPeriod)
class ExamPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "status")
    list_filter = ("status",)
    readonly_fields = ("status", "published_at")
    inlines = [PeriodSettingsInline, DesignationCapInline]


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("__str__", "period")
    list_filter = ("period",)


class RoomAllocationInline(admin.TabularInline):
    model = RoomAllocation
    extra = 0


@admin.register(ExamSession)
class ExamSessionAdmin(admin.ModelAdmin):
    list_display = ("course_code", "course_name", "department", "slot")
    list_filter = ("slot__period", "department")
    search_fields = ("course_code", "course_name")
    filter_horizontal = ("course_teachers",)
    inlines = [RoomAllocationInline]


@admin.register(RoomAllocation)
class RoomAllocationAdmin(admin.ModelAdmin):
    list_display = ("room", "session", "student_count", "invigilators_required")
    list_filter = ("session__slot__period",)
    search_fields = ("room", "session__course_code")
