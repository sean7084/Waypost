"""Early pytest plugin that mirrors ``manage.py``'s runtime bootstrap.

Loaded via ``-p waypost.pytest_bootstrap`` (see ``pyproject.toml``). pytest
resolves ``-p`` plugins during ``Config._preparse``, which happens *before*
``pytest_load_initial_conftests`` - the hook where pytest-django calls
``django.setup()``. A root ``conftest.py`` is imported too late for this: Django
has already imported the apps (and therefore WeasyPrint) by then, and on Windows
that import fails with ``cannot load library 'gobject-2.0-0'``.

Running the bootstrap here keeps ``pytest`` and ``python manage.py test``
equivalent. All three helpers are no-ops off Windows / when the relevant files or
env vars are absent, so this is safe on Linux CI too.
"""
from waypost.runtime_setup import (
    configure_windows_fontconfig,
    configure_windows_weasyprint_runtime,
    load_local_env,
)

load_local_env()
configure_windows_weasyprint_runtime()
configure_windows_fontconfig()
