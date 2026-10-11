from django import forms

from homefabric.core.forms import EntityChoiceField, VersionField
from homefabric.documents.filetypes import ACCEPT_ATTR
from homefabric.documents.models import Document, DocumentCategory


class UploadForm(forms.Form):
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": ACCEPT_ATTR}))
    display_name = forms.CharField(max_length=200, required=False,
                                   help_text="Defaults to the file name.")
    category = forms.ChoiceField(choices=DocumentCategory.choices, initial="other")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    entity = EntityChoiceField(queryset=None, required=False, label="Attach to item")

    def __init__(self, *args, household, **kwargs):
        super().__init__(*args, **kwargs)
        from homefabric.assets.models import Entity, EntityStatus

        self.fields["entity"].queryset = Entity.objects.filter(
            household=household, status=EntityStatus.ACTIVE).select_related("entity_type")


class RevisionForm(forms.Form):
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": ACCEPT_ATTR}),
                           label="Replacement file")
    note = forms.CharField(max_length=300, required=False, label="What changed?")
    version = VersionField()


class MetadataForm(forms.Form):
    display_name = forms.CharField(max_length=200)
    category = forms.ChoiceField(choices=DocumentCategory.choices)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    version = VersionField()


class AttachForm(forms.Form):
    document = forms.ModelChoiceField(queryset=Document.objects.none(), label="Existing document")

    def __init__(self, *args, household, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document"].queryset = Document.objects.filter(household=household)


class DocumentFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search names, descriptions and file names")
    category = forms.ChoiceField(required=False,
                                 choices=[("", "All categories"), *DocumentCategory.choices])
