import datetime as dt

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from homefabric.core import money
from homefabric.core.dates import get_zone
from homefabric.core.text import render_markdown

register = template.Library()


@register.filter
def markdown(text):
    return render_markdown(text)


@register.simple_tag
def money_display(minor, currency):
    return money.format_money(minor, currency)


@register.filter
def filesize(n):
    if n is None:
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


@register.simple_tag(takes_context=True)
def localtime_hh(context, value, fmt="%d %b %Y %H:%M"):
    """Format a UTC timestamp in the current household timezone."""
    if not value:
        return ""
    hh = context.get("current_household")
    zone = get_zone(hh.timezone) if hh else dt.UTC
    return value.astimezone(zone).strftime(fmt)


STATUS_CLASSES = {
    "open": "info", "in_progress": "info", "completed": "ok", "skipped": "muted",
    "cancelled": "muted", "active": "ok", "archived": "muted", "sent": "ok", "failed": "bad",
    "pending": "info", "leased": "info", "suppressed": "muted", "batched": "muted",
    "review": "warn", "enabled": "ok", "disabled": "muted",
}


@register.simple_tag
def status_badge(value, label=None):
    return format_html('<span class="badge badge-{}">{}</span>',
                       STATUS_CLASSES.get(value, "muted"), label or str(value).replace("_", " ").capitalize())


@register.simple_tag
def due_badge(task, today):
    if not task.due_date:
        return mark_safe('<span class="due due-none">No due date</span>')
    if task.is_unfinished and task.due_date < today:
        days = (today - task.due_date).days
        return format_html('<span class="due due-overdue"><span class="icon" aria-hidden="true">'
                           '!</span> Overdue by {} day{} ({})</span>', days,
                           "" if days == 1 else "s", task.due_date.strftime("%d %b %Y"))
    if task.is_unfinished and task.due_date == today:
        return mark_safe('<span class="due due-today">Due today</span>')
    if task.is_unfinished and (task.due_date - today).days <= 14:
        return format_html('<span class="due due-soon">Due {}</span>',
                           task.due_date.strftime("%a %d %b %Y"))
    return format_html('<span class="due">Due {}</span>', task.due_date.strftime("%d %b %Y"))


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """Rebuild the current querystring replacing the given keys (for pagination)."""
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.filter
def kind_icon(kind):
    return {"property": "⌂", "floor": "▤", "room": "▢", "system": "⚙", "equipment": "◆",
            "component": "◇", "policy": "§", "agreement": "✎"}.get(kind, "•")


@register.simple_tag(takes_context=True)
def page_query(context, number, param="page"):
    query = context["request"].GET.copy()
    query[param or "page"] = number
    return query.urlencode()
