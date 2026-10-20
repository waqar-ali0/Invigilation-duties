from django.contrib import admin

from .models import Department, Faculty


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "hod")
    search_fields = ("name",)
    autocomplete_fields = ("hod",)


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ("name", "employee_id", "department", "designation", "is_active", "is_exempt")
    list_filter = ("department", "designation", "is_active", "is_exempt")
    search_fields = ("name", "email", "employee_id")
    autocomplete_fields = ("user",)
