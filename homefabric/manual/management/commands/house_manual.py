from homefabric.accounts.models import Household
from homefabric.core.cli import FabricCommand
from homefabric.manual.render import write_html, write_zip


class Command(FabricCommand):
    help = ("Write a printable house manual for one household: a single offline HTML file, "
            "or a ZIP containing the manual and its documents (--output ending in .zip).")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("household", help="Household ID or exact name.")
        parser.add_argument("--output", required=True, help="File ending in .html or .zip.")
        parser.add_argument("--history", choices=["recent", "all", "none"], default="recent")
        parser.add_argument("--no-photos", action="store_true")
        parser.add_argument("--continuous", action="store_true",
                            help="Do not start each item on a new printed page.")
        parser.add_argument("--include-archived", action="store_true")

    def handle(self, *args, **options):
        key = options["household"]
        household = Household.objects.filter(name=key).first()
        if household is None and len(key) >= 32:
            household = Household.objects.filter(pk=key).first()
        if household is None:
            self.fail(options, "Household not found (use its ID or exact name).")
        opts = {"history": options["history"], "photos": not options["no_photos"],
                "page_per_item": not options["continuous"],
                "include_archived": options["include_archived"]}
        out = options["output"]
        path = (write_zip if out.lower().endswith(".zip") else write_html)(household, out, **opts)
        self.emit(options, {"ok": True, "output": str(path)},
                  f"House manual written to {path}. Open it in a browser and print to PDF.")
