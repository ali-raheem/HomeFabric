from homefabric.core.cli import FabricCommand
from homefabric.operations import maintenance_lock


class Command(FabricCommand):
    help = "Show or clear the maintenance (write-freeze) lock."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--clear", action="store_true",
                            help="Remove a stale lock left by an interrupted backup.")

    def handle(self, *args, **options):
        info = maintenance_lock.current()
        if options["clear"]:
            maintenance_lock.release()
            self.emit(options, {"ok": True, "cleared": info is not None},
                      "Maintenance lock cleared." if info else "No lock was held.")
            return
        self.emit(options, {"locked": info is not None, "info": info},
                  f"Locked: {info}" if info else "Not locked.")
