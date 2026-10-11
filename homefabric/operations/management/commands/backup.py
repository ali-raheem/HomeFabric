from homefabric.core.cli import FabricCommand
from homefabric.operations.archive import BackupError, create_backup
from homefabric.operations.maintenance_lock import MaintenanceLockError


class Command(FabricCommand):
    help = ("Create a full-instance backup archive (database snapshot plus every document). "
            "The archive contains password hashes: store it securely.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--output", required=True,
                            help="Archive file path, or a directory for a timestamped name.")
        parser.add_argument("--drain-seconds", type=float, default=None,
                            help="Seconds to wait for in-flight writes after freezing.")

    def handle(self, *args, **options):
        try:
            manifest = create_backup(options["output"], drain_seconds=options["drain_seconds"])
        except (BackupError, MaintenanceLockError, OSError) as exc:
            self.fail(options, f"Backup failed: {exc}")
        summary = {"ok": True, "archive": manifest["archive"], "files": len(manifest["files"]),
                   "created_at": manifest["created_at"], "app_version": manifest["app_version"]}
        self.emit(options, summary,
                  f"Backup written to {manifest['archive']} ({len(manifest['files'])} files).\n"
                  "Secrets are not included. Keep FABRIC_SECRET_KEY / provider credentials "
                  "separately for recovery.")
