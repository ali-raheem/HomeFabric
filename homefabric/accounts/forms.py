from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password

from homefabric.accounts.models import Role, User
from homefabric.core.dates import timezone_choices
from homefabric.core.forms import VersionField


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "throttled": "Too many failed sign-in attempts. Wait 15 minutes and try again, or ask "
                     "an administrator for a password link.",
    }

    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            if getattr(self.request, "login_throttled", False):
                raise forms.ValidationError(self.error_messages["throttled"],
                                            code="throttled") from None
            raise


class SignupForm(forms.Form):
    username = forms.CharField(max_length=150)
    display_name = forms.CharField(max_length=150, required=False, label="Your name")
    email = forms.EmailField(required=False, help_text="Used for password resets and reminders.")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Password (again)")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        User.username_validator(username)
        return username

    def clean(self):
        data = super().clean()
        if data.get("password1") != data.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        elif data.get("password1"):
            try:
                validate_password(data["password1"], User(username=data.get("username", ""),
                                                          email=data.get("email", "")))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["display_name", "first_name", "last_name", "email"]


class HouseholdSettingsForm(forms.Form):
    name = forms.CharField(max_length=150)
    timezone = forms.ChoiceField(help_text="Due dates and reminder times use this timezone.")
    currency = forms.CharField(max_length=3, help_text="Default currency, e.g. GBP.")
    version = VersionField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [(z, z) for z in timezone_choices()]


class HouseholdCreateForm(forms.Form):
    name = forms.CharField(max_length=150)
    timezone = forms.ChoiceField(initial="Europe/London")
    currency = forms.CharField(max_length=3, initial="GBP")
    owner = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True),
                                   required=False, help_text="Defaults to you.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [(z, z) for z in timezone_choices()]


class InviteForm(forms.Form):
    role = forms.ChoiceField(choices=Role.choices, initial=Role.EDITOR)
    email = forms.EmailField(required=False,
                             help_text="Optional. If email is configured the link is sent; "
                                       "otherwise copy the link shown after creating it.")


class RoleForm(forms.Form):
    role = forms.ChoiceField(choices=Role.choices)


class AdminSetPasswordForm(SetPasswordForm):
    pass
