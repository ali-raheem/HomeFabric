import datetime as dt
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from homefabric import __version__
from homefabric.core.cli import FabricCommand
from homefabric.operations.archive import BackupError, create_backup


class Command(FabricCommand):
    help = ("Apply database migrations after installing a new release, taking a verified "
            "backup first. Downgrades use a prior backup, never reverse migrations.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--backup-dir", default=None,
                            help="Where to write the pre-upgrade backup (default DATA_DIR/backups).")
        parser.add_argument("--skip-backup", action="store_true",
                            help="Not recommended: migrate without a backup.")

    def handle(self, *args, **options):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            self.emit(options, {"ok": True, "applied": 0}, "Database is up to date.")
            return
        archive = None
        if not options["skip_backup"]:
            directory = Path(options["backup_dir"] or settings.DATA_DIR / "backups")
            directory.mkdir(parents=True, exist_ok=True)
            name = f"pre-upgrade-{__version__}-{dt.datetime.now():%Y%m%d-%H%M%S}.zip"
            try:
                archive = create_backup(directory / name)["archive"]
            except (BackupError, OSError) as exc:
                self.fail(options, f"Pre-upgrade backup failed; nothing was migrated: {exc}")
            if not options.get("json"):
                self.stdout.write(f"Backup written to {archive}")
        call_command("migrate", interactive=False, verbosity=0)
        self.emit(options, {"ok": True, "applied": len(plan), "backup": archive},
                  f"Applied {len(plan)} migration(s).")
