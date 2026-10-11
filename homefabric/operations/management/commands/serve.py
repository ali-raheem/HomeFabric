from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the production web server (Waitress; works on Windows, macOS and Linux)."

    def add_arguments(self, parser):
        parser.add_argument("--bind", default=None,
                            help="host:port (default FABRIC_BIND or 127.0.0.1:8000)")
        parser.add_argument("--threads", type=int, default=8)

    def handle(self, *args, **options):
        from waitress import serve

        from homefabric.wsgi import application

        bind = options["bind"] or settings.BIND_ADDRESS
        host, _, port = bind.rpartition(":")
        host = host.strip("[]") or "127.0.0.1"
        kwargs = {
            "host": host, "port": int(port), "threads": options["threads"], "ident": "HomeFabric",
        }
        proxy = settings.CONFIG.str("TRUSTED_PROXY")
        if proxy:
            # Honour X-Forwarded-Proto/For only from the configured reverse proxy.
            kwargs.update(trusted_proxy=proxy, trusted_proxy_count=1,
                          trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for"},
                          clear_untrusted_proxy_headers=True)
        self.stdout.write(f"HomeFabric listening on http://{bind} (public URL "
                          f"{settings.PUBLIC_BASE_URL})")
        serve(application, **kwargs)
