from django import forms
from django.db.models import Q

from .models import Faculty, FacultyCategory


class FacultyForm(forms.ModelForm):
    class Meta:
        model = Faculty
        fields = [
            "employee_id", "name", "email", "phone", "department", "category", "employment_status",
            "courses", "is_active", "is_exempt", "remarks",
        ]
        widgets = {"remarks": forms.Textarea(attrs={"rows": 3}), "courses": forms.SelectMultiple(attrs={"size": 6})}
        labels = {"is_active": "Active (can be given duties)", "is_exempt": "Exempt from invigilation"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.category_id if self.instance.pk else None
        self.fields["category"].queryset = FacultyCategory.objects.filter(Q(is_active=True) | Q(pk=current))
        self.fields["category"].label = "Designation"
        if self.instance.pk:
            # The faculty ID never changes once given.
            self.fields["employee_id"].disabled = True
            self.fields["employee_id"].help_text = "Fixed once given."
        self.fields["employee_id"].required = False

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower() or None


class CategoryForm(forms.ModelForm):
    class Meta:
        model = FacultyCategory
        fields = ["name", "is_active", "is_visiting", "is_backup", "order"]
        labels = {
            "is_active": "In use",
            "is_visiting": "Visiting (duties can be set per course)",
            "is_backup": "Backup pool by default",
        }
