import os
import sys

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # The gate below stays. Gunicorn runs `--workers 3` (docker/Dockerfile) and
        # the per-mailbox lock in mailbox_sync is a process-local dict, so a
        # worker that started this thread would start three independent pollers:
        # three times the RFQ LLM spend and three times the duplicate mail
        # processing. Unattended polling is `manage.py sync_mailboxes`, driven by
        # docker/systemd/waypost-mailbox-sync.timer, which holds a cross-process
        # cache lock instead. This thread is the runserver convenience path only.
        if len(sys.argv) > 1 and sys.argv[1] != 'runserver':
            return
        if os.environ.get('RUN_MAIN') != 'true':
            return
        from .mailbox_sync import start_mailbox_sync_thread

        start_mailbox_sync_thread()
