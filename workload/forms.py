from django import forms

from exams.models import ExamPeriod, PeriodSettings
from faculty.models import Faculty

from .models import CategoryRule, FacultyRule, TargetKind, WorkloadPolicy

NUMBER = {"min": 0, "class": "num", "inputmode": "numeric"}


class DefaultsForm(forms.ModelForm):
    class Meta:
        model = PeriodSettings
        fields = ["max_duties_per_faculty", "max_duties_per_day"]
        widgets = {"max_duties_per_faculty": forms.NumberInput(attrs=NUMBER), "max_duties_per_day": forms.NumberInput(attrs=NUMBER)}
        labels = {"max_duties_per_day": "Maximum per person per day"}


class CategoryRuleForm(forms.ModelForm):
    class Meta:
        model = CategoryRule
        fields = ["target", "kind", "min_duties", "max_duties", "per_course"]
        widgets = {k: forms.NumberInput(attrs=NUMBER) for k in ("target", "min_duties", "max_duties", "per_course")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [("", "—")] + list(TargetKind.choices)
        if not self.instance.category.is_visiting:
            del self.fields["per_course"]

    def is_empty(self):
        return all(self.cleaned_data.get(k) in (None, "") for k in self.fields)


class PolicyForm(forms.ModelForm):
    class Meta:
        model = WorkloadPolicy
        fields = ["excused_absence_counts", "unexcused_absence_counts", "makeup_counts", "stand_in_counts", "combined_periods"]
        widgets = {"combined_periods": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["combined_periods"].queryset = ExamPeriod.objects.exclude(pk=self.instance.period_id)
        self.fields["combined_periods"].label = "Count duties from these periods toward the same targets"


class FacultyRuleForm(forms.ModelForm):
    class Meta:
        model = FacultyRule
        fields = ["faculty", "target", "kind", "min_duties", "max_duties", "exempt", "allow_inactive", "reason"]
        widgets = {k: forms.NumberInput(attrs=NUMBER) for k in ("target", "min_duties", "max_duties")}
        labels = {"exempt": "Exempt in this period", "allow_inactive": "Exception: allow duties although inactive"}

    def __init__(self, *args, period=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["kind"].choices = [("", "—")] + list(TargetKind.choices)
        self.fields["faculty"].queryset = Faculty.objects.select_related("category").order_by("name")
        self.fields["faculty"].label_from_instance = lambda f: f"{f.name} ({f.employee_id}, {f.category})"
        if self.instance.pk:
            self.fields["faculty"].disabled = True

    def clean(self):
        data = super().clean()
        if data.get("allow_inactive") and not (data.get("reason") or "").strip():
            self.add_error("reason", "Give the reason for this exception.")
        if self.period and data.get("faculty") and not self.instance.pk:
            if FacultyRule.objects.filter(period=self.period, faculty=data["faculty"]).exists():
                self.add_error("faculty", "This person already has a rule in this period; edit it instead.")
        return data


class CopyForm(forms.Form):
    source = forms.ModelChoiceField(ExamPeriod.objects.none(), label="Copy rules from")

    def __init__(self, *args, period=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = ExamPeriod.objects.exclude(pk=period.pk)
