"""
ASGI config for waypost project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

from waypost.runtime_setup import configure_windows_fontconfig, configure_windows_weasyprint_runtime, load_local_env

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'waypost.settings')
load_local_env()
configure_windows_weasyprint_runtime()
configure_windows_fontconfig()

application = get_asgi_application()
