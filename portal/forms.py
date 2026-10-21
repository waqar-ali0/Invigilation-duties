from django import forms


class SlotSelect(forms.Select):
    """Tags each slot option with its date, so the page can list only the chosen date's slots."""

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if value:
            option["attrs"]["data-date"] = value.instance.date.isoformat()
        return option


class SlotChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, slot):
        return f"{slot.date:%a %d %b} {slot.start_time:%H:%M}–{slot.end_time:%H:%M}"


class RequestForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    slot = SlotChoiceField(
        queryset=None, required=False, empty_label="Whole day", widget=SlotSelect(attrs={"data-slot-filter": ""})
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, period, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["slot"].queryset = period.slots.order_by("date", "start_time")
        self.fields["date"].widget.attrs.update(min=period.start_date.isoformat(), max=period.end_date.isoformat())
