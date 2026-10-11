from django.core.paginator import Paginator

DEFAULT_PAGE_SIZE = 25


def paginate(request, queryset, per_page: int = DEFAULT_PAGE_SIZE, param: str = "page"):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get(param))
