from homefabric.core.cli import FabricCommand
from homefabric.notifications import services
from homefabric.operations.models import SystemState


class Command(FabricCommand):
    help = "Show, pause or resume notification delivery."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("status")
        p = sub.add_parser("pause")
        p.add_argument("--reason", default="Paused by administrator")
        r = sub.add_parser("resume")
        r.add_argument("--public-url-verified", action="store_true",
                       help="Confirm FABRIC_PUBLIC_BASE_URL is correct for links.")
        group = r.add_mutually_exclusive_group(required=True)
        group.add_argument("--send-backlog", action="store_true")
        group.add_argument("--discard-backlog", action="store_true")

    def handle(self, *args, **options):
        state = SystemState.get()
        if options["action"] == "status":
            preview = services.resume_preview()
            payload = {"paused": state.notifications_paused, "reason": state.paused_reason,
                       "restored_at": state.restored_at, **preview,
                       **services.health_summary()}
            self.emit(options, payload, "\n".join(f"{k}: {v}" for k, v in payload.items()))
        elif options["action"] == "pause":
            services.pause(options["reason"])
            self.emit(options, {"ok": True, "paused": True}, "Notification delivery paused.")
        else:
            try:
                result = services.resume(discard_backlog=options["discard_backlog"],
                                         public_url_verified=options["public_url_verified"])
            except ValueError as exc:
                self.fail(options, f"{exc} Pass --public-url-verified after checking it.")
            self.emit(options, {"ok": True, **result},
                      f"Delivery resumed. Discarded {result['discarded']} backlog job(s).")
