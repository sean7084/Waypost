#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from waypost.runtime_setup import configure_windows_fontconfig, configure_windows_weasyprint_runtime, load_local_env


def main():
    """Run administrative tasks."""
    load_local_env()
    configure_windows_weasyprint_runtime()
    configure_windows_fontconfig()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'waypost.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
