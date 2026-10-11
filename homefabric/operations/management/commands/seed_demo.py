from homefabric.accounts import services
from homefabric.accounts.models import Household, User
from homefabric.core.cli import FabricCommand
from homefabric.operations.demo import build


class Command(FabricCommand):
    help = "Create a demo household with the boiler scenario (for evaluation and training)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--owner", required=True, help="Existing username to own the demo.")
        parser.add_argument("--name", default="Demo household")

    def handle(self, *args, **options):
        user = User.objects.filter(username__iexact=options["owner"]).first()
        if user is None:
            self.fail(options, "Owner not found.")
        if Household.objects.filter(name=options["name"]).exists():
            self.fail(options, f"A household named '{options['name']}' already exists.")
        household = services.create_household(name=options["name"], owner=user, actor=user)
        build(household, user)
        self.emit(options, {"ok": True, "household": str(household.pk)},
                  f"Demo household '{household.name}' created.")
