from homefabric.core.cli import FabricCommand
from homefabric.operations.health import readiness


class Command(FabricCommand):
    help = "Check database, data directory and scheduler health (exit 1 if not ready)."

    def handle(self, *args, **options):
        report = readiness(include_scheduler=True)
        self.emit(options, report, "\n".join(f"{k}: {v}" for k, v in report["checks"].items()))
        if not report["ok"]:
            raise SystemExit(1)
