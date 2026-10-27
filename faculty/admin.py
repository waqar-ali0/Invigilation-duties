from django.contrib import admin

from .models import Course, Department, Faculty, FacultyCategory


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "hod")
    search_fields = ("name",)
    autocomplete_fields = ("hod",)


@admin.register(FacultyCategory)
class FacultyCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "is_visiting", "is_backup", "order")
    list_editable = ("is_active", "order")
    exclude = ()
    readonly_fields = ()

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        # Categories are deactivated, never deleted, so faculty records keep their history.
        return False

    def save_model(self, request, obj, form, change):
        if not obj.code:
            from django.utils.text import slugify

            obj.code = slugify(obj.name).replace("-", "_")[:40]
        super().save_model(request, obj, form, change)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department")
    list_filter = ("department",)
    search_fields = ("code", "name")


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ("name", "employee_id", "department", "category", "phone", "is_active", "is_exempt")
    list_filter = ("department", "category", "employment_status", "is_active", "is_exempt")
    search_fields = ("name", "email", "employee_id", "phone", "phone_e164")
    readonly_fields = ("phone_e164",)
    autocomplete_fields = ("user",)
    filter_horizontal = ("courses",)

    def get_readonly_fields(self, request, obj=None):
        # The faculty ID never changes once given.
        return ("employee_id", "phone_e164") if obj else ("phone_e164",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category":
            kwargs["queryset"] = FacultyCategory.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
