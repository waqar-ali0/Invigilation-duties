from django.contrib import admin

from .models import BackupMember, BackupReservation, Duty, Preference, SwapRequest, Unavailability


@admin.register(SwapRequest)
class SwapRequestAdmin(admin.ModelAdmin):
    """Read-only list; swaps are handled on the Swaps page."""

    list_display = ("requested_by", "requested_with", "duty", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("requested_by__name", "requested_with__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Unavailability)
class UnavailabilityAdmin(admin.ModelAdmin):
    list_display = ("faculty", "period", "date", "slot", "status")
    list_filter = ("period", "status")
    search_fields = ("faculty__name", "faculty__email")
    autocomplete_fields = ("faculty",)


@admin.register(Duty)
class DutyAdmin(admin.ModelAdmin):
    """Read-only list; duties are edited through the draft review screens."""

    list_display = ("faculty", "room_allocation", "status", "source")
    list_filter = ("period", "status", "source")
    search_fields = ("faculty__name", "room_allocation__room", "room_allocation__session__course_code")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Preference)
class PreferenceAdmin(admin.ModelAdmin):
    list_display = ("faculty", "period", "kind", "date", "slot", "building")
    list_filter = ("period", "kind")
    search_fields = ("faculty__name",)


@admin.register(BackupMember)
class BackupMemberAdmin(admin.ModelAdmin):
    list_display = ("faculty", "role", "priority", "max_backup")
    list_filter = ("role",)
    search_fields = ("faculty__name",)


@admin.register(BackupReservation)
class BackupReservationAdmin(admin.ModelAdmin):
    list_display = ("faculty", "slot", "status", "duty")
    list_filter = ("period", "status")
