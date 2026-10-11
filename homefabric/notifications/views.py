from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from homefabric.core import audit
from homefabric.core.pagination import paginate
from homefabric.core.urls_util import safe_redirect
from homefabric.notifications import services
from homefabric.notifications.adapters import registry
from homefabric.notifications.forms import EndpointForm
from homefabric.notifications.models import NotificationDelivery, NotificationEndpoint


def _endpoint(request, pk):
    endpoint = NotificationEndpoint.objects.filter(pk=pk, user=request.user, deleted=False).first()
    if endpoint is None:
        raise Http404
    return endpoint


def _validate(form, endpoint):
    adapter = form.adapters[endpoint.provider]
    try:
        adapter.validate(endpoint)
    except Exception as exc:  # noqa: BLE001 - adapter validation messages are user-facing
        form.add_error(None, str(exc) or "That configuration is not valid.")
        return False
    return True


@login_required
def channel_list(request):
    endpoints = NotificationEndpoint.objects.filter(user=request.user, deleted=False)
    deliveries = NotificationDelivery.objects.filter(recipient=request.user).exclude(
        kind="reminder", state="batched").select_related("endpoint", "task")
    return render(request, "notifications/channel_list.html", {
        "endpoints": endpoints,
        "failures": services.failures_for_user(request.user)[:20],
        "page": paginate(request, deliveries.order_by("-created_at"), per_page=20),
        "adapters": registry(),
        "paused": services.is_paused(),
    })


@login_required
def channel_create(request):
    form = EndpointForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        endpoint = NotificationEndpoint(
            user=request.user, provider=d["provider"], display_name=d["display_name"],
            address=d.get("address") or "", secret_value=d.get("secret_value") or "",
            secret_ref=d.get("secret_ref") or "")
        if _validate(form, endpoint):
            endpoint.save()
            audit.record(request.user, "endpoint.create", endpoint, label=str(endpoint))
            ok, error = services.send_test(endpoint, actor=request.user)
            if ok:
                messages.success(request, "Channel added and a test message was sent.")
            else:
                messages.warning(request, f"Channel added, but the test message failed: {error}")
            return redirect("notifications:channels")
    return render(request, "notifications/channel_form.html", {"form": form})


@login_required
def channel_edit(request, pk):
    endpoint = _endpoint(request, pk)
    form = EndpointForm(request.POST or None, editing=True, initial={
        "provider": endpoint.provider, "display_name": endpoint.display_name,
        "address": endpoint.address, "secret_ref": endpoint.secret_ref})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        endpoint.display_name = d["display_name"]
        endpoint.address = d.get("address") or ""
        if d.get("secret_value"):
            endpoint.secret_value = d["secret_value"]
            endpoint.secret_ref = ""
        elif d.get("secret_ref"):
            endpoint.secret_ref = d["secret_ref"]
            endpoint.secret_value = ""
        if _validate(form, endpoint):
            endpoint.save()
            audit.record(request.user, "endpoint.update", endpoint, label=str(endpoint))
            messages.success(request, "Channel saved. Send a test to verify it.")
            return redirect("notifications:channels")
    return render(request, "notifications/channel_form.html", {"form": form,
                                                               "endpoint": endpoint})


@require_POST
@login_required
def channel_test(request, pk):
    endpoint = _endpoint(request, pk)
    ok, error = services.send_test(endpoint, actor=request.user)
    if ok:
        messages.success(request, f"Test message sent via {endpoint.display_name}.")
    else:
        messages.error(request, f"Test failed: {error}")
    return redirect("notifications:channels")


@require_POST
@login_required
def channel_toggle(request, pk):
    endpoint = _endpoint(request, pk)
    endpoint.enabled = not endpoint.enabled
    endpoint.save(update_fields=["enabled"])
    messages.success(request, f"Channel {'enabled' if endpoint.enabled else 'disabled'}.")
    return redirect("notifications:channels")


@require_POST
@login_required
def channel_delete(request, pk):
    endpoint = _endpoint(request, pk)
    endpoint.deleted = True
    endpoint.enabled = False
    endpoint.secret_value = ""
    endpoint.save(update_fields=["deleted", "enabled", "secret_value"])
    audit.record(request.user, "endpoint.delete", endpoint, label=str(endpoint))
    messages.success(request, "Channel removed.")
    return redirect("notifications:channels")


@require_POST
@login_required
def acknowledge(request, pk):
    qs = NotificationDelivery.objects.filter(pk=pk)
    if not (request.user.is_instance_admin or (
            getattr(request, "role", None) in ("owner", "admin")
            and qs.filter(household=request.household).exists())):
        qs = qs.filter(recipient=request.user)
    qs.update(acknowledged=True)
    return safe_redirect(request, request.POST.get("next"), "notifications:channels")
