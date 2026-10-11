from django.shortcuts import render


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", {"message": str(exception or "")}, status=403)


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)
