import re

from django import forms

from homefabric.notifications.adapters import registry

ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,119}$")


class EndpointForm(forms.Form):
    provider = forms.ChoiceField(label="Channel")
    display_name = forms.CharField(max_length=100, help_text="For example 'My phone'.")
    address = forms.EmailField(required=False, label="Email address")
    secret_value = forms.CharField(
        required=False, label="Apprise URL", widget=forms.PasswordInput(render_value=False),
        help_text="Stored privately and never shown again. Leave blank to keep the current value.")
    secret_ref = forms.CharField(
        required=False, max_length=120, label="…or environment variable name",
        help_text="Advanced: read the URL from this server environment variable instead.")

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.adapters = registry()
        self.fields["provider"].choices = [(k, a.label) for k, a in self.adapters.items()]
        self.editing = editing
        if editing:
            self.fields["provider"].disabled = True

    def clean_secret_ref(self):
        value = self.cleaned_data.get("secret_ref", "").strip()
        if value and not ENV_NAME.match(value):
            raise forms.ValidationError("Use an upper-case environment variable name.")
        return value

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        adapter = self.adapters.get(provider)
        if adapter is None:
            return data
        if provider == "email" and not data.get("address"):
            self.add_error("address", "Enter the email address to notify.")
        if adapter.needs_secret and not self.editing and not (
                data.get("secret_value") or data.get("secret_ref")):
            self.add_error("secret_value", f"Enter the {adapter.secret_label}.")
        return data
