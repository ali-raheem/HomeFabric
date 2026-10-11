from homefabric.core.cli import FabricCommand
from homefabric.operations.archive import verify_archive


class Command(FabricCommand):
    help = "Verify a backup archive's structure, checksums, schema and database integrity."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("archive")

    def handle(self, *args, **options):
        result = verify_archive(options["archive"])
        m = result.manifest or {}
        payload = {"ok": result.ok, "errors": result.errors, "warnings": result.warnings,
                   "created_at": m.get("created_at"), "app_version": m.get("app_version"),
                   "files": len(m.get("files", [])), "counts": m.get("counts")}
        if result.ok:
            self.emit(options, payload,
                      f"Archive OK: created {m.get('created_at')} by HomeFabric "
                      f"{m.get('app_version')}, {len(m.get('files', []))} files.")
        else:
            self.fail(options, "Archive invalid: " + "; ".join(result.errors), payload)
