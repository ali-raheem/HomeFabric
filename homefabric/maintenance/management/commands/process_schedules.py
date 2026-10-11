from homefabric.core.cli import FabricCommand
from homefabric.maintenance.services import run_all_schedules
from homefabric.operations.jobs import JobSkipped, run_job


class Command(FabricCommand):
    help = ("Generate due task occurrences for all enabled schedules. Run every minute from "
            "cron, a systemd timer, or Windows Task Scheduler.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--once", action="store_true",
                            help="Run a single pass (the default; accepted for clarity).")

    def handle(self, *args, **options):
        try:
            result = run_job("process_schedules", run_all_schedules)
        except JobSkipped as exc:
            self.emit(options, {"ok": True, "skipped": str(exc)}, f"Skipped: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.fail(options, f"process_schedules failed: {exc}")
        self.emit(options, {"ok": not result["errors"], **result},
                  f"{result['schedules']} schedule(s) checked, {result['created']} occurrence(s) "
                  f"created." + (f" Errors: {result['errors']}" if result["errors"] else ""))
        if result["errors"]:
            raise SystemExit(1)
