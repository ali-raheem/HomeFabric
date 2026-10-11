from django.forms.renderers import TemplatesSetting


class FabricFormRenderer(TemplatesSetting):
    form_template_name = "partials/form.html"
    field_template_name = "partials/field.html"
