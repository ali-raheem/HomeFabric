from homefabric.core.cli import FabricCommand
from homefabric.operations.integrity import check


class Command(FabricCommand):
    help = "Report database problems, missing files, and orphaned data."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--verify-checksums", action="store_true",
                            help="Re-hash every stored file (slow on large instances).")
        parser.add_argument("--clean", action="store_true",
                            help="Delete orphaned files and stale staging files.")

    def handle(self, *args, **options):
        report = check(verify_checksums=options["verify_checksums"], clean=options["clean"])
        lines = [f"[{p['kind']}] {p['detail']}" for p in report["problems"]]
        lines += [f"cleaned: {c}" for c in report["cleaned"]]
        lines.append("Integrity OK." if report["ok"] else
                     f"{len(report['problems'])} problem(s) found.")
        self.emit(options, report, "\n".join(lines))
        if not report["ok"]:
            raise SystemExit(1)
