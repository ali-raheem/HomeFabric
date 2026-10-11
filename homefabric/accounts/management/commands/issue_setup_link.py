from homefabric.accounts.models import User
from homefabric.accounts.services import password_setup_url
from homefabric.core.cli import FabricCommand


class Command(FabricCommand):
    help = ("Print a single-use password set/reset link for a user. Use for account "
            "recovery when email is not configured.")

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("username")

    def handle(self, *args, **options):
        user = User.objects.filter(username__iexact=options["username"]).first()
        if user is None:
            self.fail(options, "No such user.")
        if not user.is_active:
            self.fail(options, "That user is deactivated; reactivate them first.")
        url = password_setup_url(user)
        self.emit(options, {"ok": True, "username": user.username, "url": url},
                  f"Password link for {user.username} (valid {24} hours, single use):\n{url}")
