from django import forms


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class TimeInput(forms.TimeInput):
    input_type = "time"

    def __init__(self, **kwargs):
        super().__init__(format="%H:%M", **kwargs)


class VersionField(forms.IntegerField):
    widget = forms.HiddenInput

    def __init__(self, **kwargs):
        super().__init__(min_value=1, **kwargs)


class MoneyMixin(forms.Form):
    amount = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=3,
                                label="Cost")
    currency = forms.CharField(required=False, max_length=3, label="Currency",
                               help_text="Three-letter code, e.g. GBP.")

    def clean_currency(self):
        value = (self.cleaned_data.get("currency") or "").strip().upper()
        if value and (len(value) != 3 or not value.isalpha()):
            raise forms.ValidationError("Use a three-letter currency code such as GBP.")
        return value


class EntityChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} ({obj.entity_type.name})"
