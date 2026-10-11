"""Console entry point: ``homefabric <command>`` is equivalent to ``manage.py <command>``."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "homefabric.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line([sys.argv[0], *sys.argv[1:]])


if __name__ == "__main__":
    main()
