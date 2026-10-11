from homefabric.core.cli import FabricCommand
from homefabric.notifications.services import process
from homefabric.operations.jobs import JobSkipped, run_job


class Command(FabricCommand):
    help = "Plan due reminders and deliver pending notifications with retries."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--once", action="store_true",
                            help="Run a single pass (the default; accepted for clarity).")

    def handle(self, *args, **options):
        try:
            result = run_job("process_notifications", process)
        except JobSkipped as exc:
            self.emit(options, {"ok": True, "skipped": str(exc)}, f"Skipped: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.fail(options, f"process_notifications failed: {exc}")
        p, d = result["planned"], result["dispatch"]
        text = ("Notifications paused; nothing sent." if d.get("paused") else
                f"Planned {p['individual']} reminder(s), {p['summaries']} catch-up summary(ies). "
                f"Sent {d['sent']}, retrying {d['retry']}, failed {d['failed']}, "
                f"suppressed {d['suppressed']}.")
        self.emit(options, {"ok": True, **result}, text)
