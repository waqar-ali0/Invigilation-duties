from django.contrib import admin

from .models import OutboundEmail


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = ("created_at", "to", "kind", "status", "attempts")
    list_filter = ("status", "kind")
    search_fields = ("to", "subject")
    exclude = ("attachment_content",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
