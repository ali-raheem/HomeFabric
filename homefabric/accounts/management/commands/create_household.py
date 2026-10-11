from homefabric.accounts import services
from homefabric.accounts.models import User
from homefabric.core.cli import FabricCommand
from homefabric.core.errors import DomainError


class Command(FabricCommand):
    help = "Create a household with an existing user as its owner."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("name")
        parser.add_argument("--owner", required=True, help="Username of the first owner.")
        parser.add_argument("--timezone")
        parser.add_argument("--currency")

    def handle(self, *args, **options):
        owner = User.objects.filter(username__iexact=options["owner"], is_active=True).first()
        if owner is None:
            self.fail(options, "Owner user not found or inactive.")
        try:
            household = services.create_household(
                name=options["name"], owner=owner, timezone_name=options["timezone"],
                currency=options["currency"])
        except DomainError as exc:
            self.fail(options, str(exc))
        self.emit(options, {"ok": True, "id": str(household.pk)},
                  f"Household '{household.name}' created ({household.pk}).")
