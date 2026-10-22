"""Poll the mailboxes as a dedicated process, so RFQ mail is ingested unattended.

Production has never polled mailboxes: `accounts/apps.py` starts the sync thread
only under `runserver`, and Gunicorn never matches that gate, so mail was
classified only when someone opened their mailbox view (issue #24). This command
is the missing half, meant to be driven by a timer -
`docker/systemd/waypost-mailbox-sync.timer` - which runs `--once`.

Overlapping passes are the failure mode to design against, because one pass can
reach the RFQ LLM and a duplicated pass therefore duplicates real spend. Two
things prevent it: `--max-runtime` bounds the work so a slow tick cannot run
into the next one, and the pass holds a lock in the Django cache so a second
process declines instead of racing it. The cache is used rather than a database
advisory lock or `fcntl` because this command must also run against SQLite on
Windows, where `pg_try_advisory_lock` and `fcntl` do not exist, and because the
production cache is Redis - shared by all three web workers and this process.

Usage:
  python manage.py sync_mailboxes --once --max-runtime 240
  python manage.py sync_mailboxes --loop --interval 300
  python manage.py sync_mailboxes --mailbox 42
"""
import os
import time
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts import mailbox_sync
from accounts.models import LlmCallRecord, UserMailboxSettings

SYNC_LOCK_KEY = 'waypost:mailbox-sync:lock'
# A lock outliving the interval it guards would strand mailboxes after a killed
# process until it expired; a short one would release while a pass still runs.
LOCK_LEASE_GRACE_SECONDS = 60


class Command(BaseCommand):
    help = ('Sync every active mailbox once (default) or on an interval, so RFQ '
            'mail is processed with nobody logged in.')

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='Run one pass and exit (the default; this is what the timer runs)')
        parser.add_argument('--loop', action='store_true',
                            help='Keep polling every --interval seconds until interrupted')
        parser.add_argument('--interval', type=int, default=mailbox_sync.SYNC_INTERVAL_SECONDS,
                            help='Seconds between passes in --loop mode (default: %(default)s)')
        parser.add_argument('--max-runtime', type=int, default=0,
                            help='Hard stop in seconds for one pass, so a tick cannot overlap the next')
        parser.add_argument('--mailbox', type=int, default=None, metavar='PK',
                            help='Sync one mailbox by primary key, for diagnosis')
        parser.add_argument('--prune-llm-records', type=int, default=180, metavar='DAYS',
                            help='Delete LLM call records older than this many days (0 keeps them; default: %(default)s)')

    def _prune_llm_records(self, days):
        if days <= 0:
            return 0
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _counts = LlmCallRecord.objects.filter(created_at__lt=cutoff).delete()
        return deleted

    def _sync_one_mailbox(self, pk):
        mailbox_settings = UserMailboxSettings.objects.filter(pk=pk).first()
        if not mailbox_settings:
            raise CommandError(f'No mailbox with primary key {pk}.')
        counts = mailbox_sync.sync_mailbox_messages(mailbox_settings)
        return 1, sum(counts.values())

    def _run_pass(self, options):
        """One locked pass: prune, then sync. A refused tick does no work at all."""
        token = f'{os.getpid()}:{time.monotonic()}'
        if not cache.add(SYNC_LOCK_KEY, token, timeout=options['interval'] + LOCK_LEASE_GRACE_SECONDS):
            self.stderr.write('Another mailbox sync holds the lock; nothing done.')
            return
        try:
            pruned = self._prune_llm_records(options['prune_llm_records'])
            if pruned:
                self.stdout.write(f'pruned {pruned} LLM call record(s) older than {options["prune_llm_records"]} day(s)')
            if options['mailbox'] is not None:
                synced_mailboxes, synced_messages = self._sync_one_mailbox(options['mailbox'])
            else:
                deadline = time.monotonic() + options['max_runtime'] if options['max_runtime'] else None
                synced_mailboxes, synced_messages = mailbox_sync.sync_all_active_mailboxes(deadline=deadline)
            self.stdout.write(f'synced {synced_mailboxes} mailbox(es), {synced_messages} new message(s)')
        finally:
            # Only release our own lease. A pass that outlived its timeout let a
            # second syncer take the lock, and an unguarded delete would then
            # release *theirs* and admit a third.
            if cache.get(SYNC_LOCK_KEY) == token:
                cache.delete(SYNC_LOCK_KEY)

    def handle(self, *args, **options):
        if options['once'] and options['loop']:
            raise CommandError('--once and --loop are mutually exclusive.')

        if not options['loop']:
            self._run_pass(options)
            return

        # --loop exists for a host where a long-running unit is simpler than a
        # timer. The lock is taken per pass rather than once for the process: a
        # lease cannot be held honestly across an unbounded loop, and a pass
        # that outlives its lease would let a second syncer in.
        while True:
            self._run_pass(options)
            time.sleep(options['interval'])
