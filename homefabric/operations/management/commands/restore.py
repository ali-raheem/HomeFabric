from homefabric.core.cli import FabricCommand
from homefabric.operations import restore as restore_mod


class Command(FabricCommand):
    help = ("Replace this instance with a backup archive. Stop the web server and scheduler "
            "first. Use --dry-run to validate without changing anything.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("archive", nargs="?")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--confirm", action="store_true",
                          help="Perform the restore (full replacement, no merge).")
        mode.add_argument("--recover", action="store_true",
                          help="Roll back an interrupted restore using its journal.")

    def handle(self, *args, **options):
        if options["recover"]:
            message = restore_mod.recover()
            self.emit(options, {"ok": True, "message": message}, message)
            return
        if not options["archive"]:
            self.fail(options, "An archive path is required.")
        if options["dry_run"]:
            result = restore_mod.dry_run(options["archive"])
            m = result.manifest or {}
            payload = {"ok": result.ok, "errors": result.errors, "created_at": m.get("created_at"),
                       "app_version": m.get("app_version"),
                       "uncompressed_bytes": result.uncompressed_size,
                       "required_secrets": m.get("required_secrets")}
            if not result.ok:
                self.fail(options, "Dry run failed: " + "; ".join(result.errors), payload)
            self.emit(options, payload,
                      f"Dry run OK. Archive from {m.get('created_at')} (HomeFabric "
                      f"{m.get('app_version')}) can be restored. No data was changed.")
            return
        try:
            result = restore_mod.restore(options["archive"])
        except restore_mod.RestoreError as exc:
            self.fail(options, str(exc))
        self.emit(options, {"ok": True, **result}, (
            "Restore complete.\n"
            f"- Previous data kept at: {result['rollback_copy']} (delete once verified)\n"
            "- All sessions were signed out; invitations and reset links are invalid.\n"
            "- Notifications are PAUSED. Re-enter channel credentials, check the public URL,\n"
            "  then resume from Administration or 'manage.py notifications resume'."))
