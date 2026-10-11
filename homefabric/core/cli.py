"""Shared helpers for management commands: JSON output and exit codes."""

import json
import sys

from django.core.management.base import BaseCommand, CommandError


class FabricCommand(BaseCommand):
    json_option = True

    def add_arguments(self, parser):
        if self.json_option:
            parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def emit(self, options, payload: dict, text: str | None = None):
        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        elif text is not None:
            self.stdout.write(text)

    def fail(self, options, message: str, payload: dict | None = None, code: int = 1):
        if options.get("json"):
            self.stdout.write(json.dumps({"ok": False, "error": message, **(payload or {})},
                                         indent=2, default=str))
            sys.exit(code)
        raise CommandError(message, returncode=code)
