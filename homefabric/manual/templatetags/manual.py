from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def ref(r):
    """A cross-reference: linked name with its section number, and an optional note."""
    if r.anchor:
        out = format_html('<a href="#{}">{}</a> <span class="ref">({})</span>', r.anchor, r.name,
                          r.number)
    else:
        out = format_html("{}", r.name)
    if r.note:
        out += format_html(' <span class="muted">— {}</span>', r.note)
    return out
