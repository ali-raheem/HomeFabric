from homefabric.accounts import households
from homefabric.accounts.models import Household
from homefabric.core.cli import FabricCommand
from homefabric.core.errors import DomainError


class Command(FabricCommand):
    help = ("List, archive, restore or permanently delete households. Deletion requires the "
            "household to be archived and its exact name passed to --confirm-name.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("list")
        a = sub.add_parser("archive")
        a.add_argument("household")
        a.add_argument("--reason", default="")
        r = sub.add_parser("restore")
        r.add_argument("household")
        d = sub.add_parser("delete")
        d.add_argument("household")
        d.add_argument("--confirm-name", required=True,
                       help="The household's exact name, as a safeguard.")
        d.add_argument("--deactivate-sole-members", action="store_true")

    def _get(self, options):
        key = options["household"]
        household = Household.objects.filter(name=key).first()
        if household is None and len(key) >= 32:
            household = Household.objects.filter(pk=key).first()
        if household is None:
            self.fail(options, "Household not found (use its ID or exact name).")
        return household

    def handle(self, *args, **options):
        action = options["action"]
        if action == "list":
            rows = [{"id": str(h.pk), "name": h.name, "archived": h.is_archived,
                     **households.summary(h)} for h in Household.objects.order_by("name")]
            self.emit(options, {"households": rows}, "\n".join(
                f"{r['id']}  {r['name']}{'  [archived]' if r['archived'] else ''}  "
                f"members={r['members']} items={r['items']} documents={r['documents']}"
                for r in rows) or "No households.")
            return
        household = self._get(options)
        try:
            if action == "archive":
                households.archive_household(household, actor=None, reason=options["reason"])
                msg = f"Archived '{household.name}'."
                result = {}
            elif action == "restore":
                households.restore_household(household, actor=None)
                msg = f"Restored '{household.name}'."
                result = {}
            else:
                result = households.delete_household(
                    household, actor=None, confirm_name=options["confirm_name"],
                    deactivate_sole_members=options["deactivate_sole_members"])
                msg = f"Permanently deleted '{result['name']}'."
        except DomainError as exc:
            self.fail(options, str(exc))
        self.emit(options, {"ok": True, **result}, msg)
