import getpass
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from homefabric.accounts import services
from homefabric.accounts.models import Household, User
from homefabric.core.cli import FabricCommand


class Command(FabricCommand):
    help = ("Create the first instance administrator (and optionally a first household). "
            "The password is read interactively or from stdin, never from arguments.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--display-name", default="")
        parser.add_argument("--household", help="Create a household owned by this user.")
        parser.add_argument("--timezone", default=None)
        parser.add_argument("--currency", default=None)
        parser.add_argument("--password-stdin", action="store_true",
                            help="Read the password from standard input.")
        parser.add_argument("--allow-additional", action="store_true",
                            help="Allow creating an administrator when one already exists.")

    def handle(self, *args, **options):
        if User.objects.filter(is_superuser=True).exists() and not options["allow_additional"]:
            self.fail(options, "An instance administrator already exists. Use "
                      "--allow-additional to create another.")
        if User.objects.filter(username__iexact=options["username"]).exists():
            self.fail(options, "That username is already taken.")
        if options["password_stdin"]:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                self.fail(options, "Passwords do not match.")
        user = User(username=options["username"], email=options["email"],
                    display_name=options["display_name"])
        try:
            validate_password(password, user)
        except ValidationError as exc:
            self.fail(options, " ".join(exc.messages))
        with transaction.atomic():
            user.is_superuser = True
            user.is_staff = True
            user.set_password(password)
            user.save()
            household = None
            if options["household"]:
                household = services.create_household(
                    name=options["household"], owner=user, actor=user,
                    timezone_name=options["timezone"], currency=options["currency"])
        payload = {"ok": True, "username": user.username,
                   "household": str(household.pk) if household else None,
                   "households_total": Household.objects.count()}
        self.emit(options, payload, f"Instance administrator '{user.username}' created."
                  + (f" Household '{household.name}' created." if household else ""))
