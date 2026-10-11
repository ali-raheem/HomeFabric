from django import forms


class ManualOptionsForm(forms.Form):
    history = forms.ChoiceField(
        initial="recent", widget=forms.RadioSelect, label="History on each page",
        choices=[("recent", "Five most recent entries"), ("all", "Complete history"),
                 ("none", "No history")])
    photos = forms.BooleanField(required=False, initial=True,
                                label="Show photos attached to items")
    page_per_item = forms.BooleanField(required=False, initial=True,
                                       label="Start each item on a new printed page")
    include_archived = forms.BooleanField(required=False, initial=False,
                                          label="Include archived items")

    def builder_options(self) -> dict:
        d = self.cleaned_data
        return {"history": d.get("history") or "recent", "photos": bool(d.get("photos")),
                "page_per_item": bool(d.get("page_per_item")),
                "include_archived": bool(d.get("include_archived"))}
