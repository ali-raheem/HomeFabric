from homefabric.accounts.models import Household
from homefabric.core.cli import FabricCommand
from homefabric.operations.export import write_export


class Command(FabricCommand):
    help = "Export one household as JSON, CSV summaries and documents (no credentials)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("household_id")
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        household = Household.objects.filter(pk=options["household_id"]).first() if len(
            options["household_id"]) >= 32 else None
        if household is None:
            household = Household.objects.filter(name=options["household_id"]).first()
        if household is None:
            self.fail(options, "Household not found (use its ID or exact name).")
        path = write_export(household, options["output"])
        self.emit(options, {"ok": True, "output": str(path)}, f"Export written to {path}")
