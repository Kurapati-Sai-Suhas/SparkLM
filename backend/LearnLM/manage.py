#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "LearnLM.settings")

    # Console encoding, before any command can write (M2 P2.28). On Windows
    # stdout defaults to cp1252, which cannot encode the em dashes, arrows and
    # bullets these commands print — and `question_approve` writes one AFTER
    # committing its approval row, so the operator saw a traceback on a
    # transition that had already succeeded. Fixed at the stream, once, rather
    # than by rewording 34 commands. No-op on Linux, where stdout is already
    # UTF-8. Imported here so a failure to import Django is still reported by
    # the handler below rather than by this line.
    from common.console import use_utf8_console
    use_utf8_console()

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
