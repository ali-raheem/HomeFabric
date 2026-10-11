import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Run process_schedules and process_notifications every interval in a loop. "
            "Intended for containers; prefer cron/systemd/Task Scheduler on hosts.")

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        while True:
            started = time.monotonic()
            for command in ("process_schedules", "process_notifications"):
                try:
                    call_command(command)
                except SystemExit:
                    pass
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    self.stderr.write(f"{command} failed: {exc}")
            elapsed = time.monotonic() - started
            time.sleep(max(1, options["interval"] - elapsed))
