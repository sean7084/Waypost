"""App configuration for the hongwen app."""
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HongwenConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hongwen'
    verbose_name = _('Hongwen Helpdesk')