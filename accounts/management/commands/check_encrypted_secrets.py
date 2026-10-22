"""Audit every stored credential that can no longer be decrypted.

``accounts.crypto.decrypt_secret`` never raises - an unreadable credential reads
back as an empty password - so the only symptom in production is a mailbox that
"cannot connect", and the only record of the cause is a WARNING in the
application log. This command turns that into an explicit, whole-database answer
before a user notices: for each table/field that stores an encrypted secret it
reports ``total / fernet / legacy / unreadable / empty``.

The four buckets are the four things ``decrypt_secret`` can actually do with a
value, and they have different urgencies:

* ``fernet`` - reads back under the current ``DJANGO_FIELD_ENCRYPTION_KEY``. Fine.
* ``legacy`` - reads back through the XOR fallback, so no user sees a failure,
  but it is the pre-Fernet obfuscation migration ``accounts/migrations/0019_*``
  never reached. Re-encrypt it; it is not an outage.
* ``unreadable`` - fails under BOTH schemes, so this row really is an empty
  password to its owner. This is the only bucket that exits non-zero.
* ``empty`` - no credential stored, which is a normal state for an unset mailbox.

Counting a ``legacy`` row as a failure (which a single Fernet-only check would
do) makes a working mailbox look like a broken one and pages on data that is
readable; it is reported as its own bucket instead so it can still be tracked.

Fix for either non-clean bucket: open the mailbox/SMTP settings in the admin UI
and re-save the credential, which re-encrypts it under the current key.

This command prints counts and primary keys only. It never prints, logs or
returns credential material - not the stored token, not the plaintext, not the
key - so its output can be pasted into an issue or CI log safely.

Deliberately NOT wired into ``/healthz/``: that probe is what
``docker/bin/ci-deploy.sh`` polls to decide whether to roll a release back, and an
undecryptable mailbox credential is a data problem, not a broken deployment - it
must not 503 the probe and revert a good release.

Usage:
  python manage.py check_encrypted_secrets
  python manage.py check_encrypted_secrets --json
"""
import json

from django.apps import apps
from django.core.management.base import BaseCommand

from accounts.crypto import secret_scheme

# Every table/field that stores an encrypted secret. A new `encrypted_*` column
# belongs here as well, otherwise this command reports all-clear on data it never
# looked at.
SECRET_FIELDS = [
    ('accounts.UserMailboxSettings', 'encrypted_password'),
    ('accounts.SystemSMTPSettings', 'encrypted_password'),
]

COUNT_KEYS = ('total', 'fernet', 'legacy', 'unreadable', 'empty')


def _split_label(label):
    """`('accounts.UserMailboxSettings', 'encrypted_password')` for a model label."""
    app_label, _, model_name = label.rpartition('.')
    return app_label, model_name


def audit_secret_fields():
    """Return one report dict per secret field, plus a totals dict.

    Keys are field reports in ``SECRET_FIELDS`` order; the trailing ``totals``
    entry aggregates them. Values are ints and primary keys only.
    """
    reports = []
    totals = dict.fromkeys(COUNT_KEYS, 0)
    for label, field in SECRET_FIELDS:
        app_label, model_name = _split_label(label)
        model = apps.get_model(app_label, model_name)
        counts = dict.fromkeys(COUNT_KEYS[1:], 0)
        unreadable_pks, legacy_pks = [], []
        for pk, stored in model.objects.values_list('pk', field):
            scheme = secret_scheme(stored)
            if scheme is None:
                # Empty and unreadable both say "no usable credential", but only
                # one of them is a bug, so split on whether anything was stored.
                if stored:
                    counts['unreadable'] += 1
                    unreadable_pks.append(pk)
                else:
                    counts['empty'] += 1
            else:
                counts[scheme] += 1
                if scheme == 'legacy':
                    legacy_pks.append(pk)
        report = {'model': label, 'field': field}
        report['total'] = sum(counts.values())
        report.update(counts)
        report['unreadable_pks'] = unreadable_pks
        report['legacy_pks'] = legacy_pks
        reports.append(report)
        for key in totals:
            totals[key] += report[key]
    return reports, totals


def _line(report):
    counts = '  '.join(f'{key} {report[key]}' for key in COUNT_KEYS)
    return f"{report['model']}.{report['field']}: {counts}"


class Command(BaseCommand):
    help = ('Report total / fernet / legacy / unreadable / empty for every stored '
            'encrypted credential. Exits non-zero if any row is unreadable.')

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true',
                            help='Emit machine-readable JSON instead of text')

    def handle(self, *args, **options):
        reports, totals = audit_secret_fields()

        if options['json']:
            self.stdout.write(json.dumps({
                'fields': reports,
                'totals': totals,
                # 'ok' means "every stored credential reads back". A legacy row is
                # readable, so it does not flip this flag; it is counted in
                # totals['legacy'] and the advisory list for the cleanup pass.
                'ok': totals['unreadable'] == 0,
            }, ensure_ascii=False, indent=2))
        else:
            for report in reports:
                self.stdout.write(_line(report))
                if report['unreadable_pks']:
                    pks = ', '.join(str(pk) for pk in report['unreadable_pks'])
                    self.stdout.write(f"  unreadable row pks: {pks}")
                if report['legacy_pks']:
                    pks = ', '.join(str(pk) for pk in report['legacy_pks'])
                    self.stdout.write(f"  legacy pre-Fernet row pks: {pks}")
            self.stdout.write(f"TOTAL: {'  '.join(f'{k} {totals[k]}' for k in COUNT_KEYS)}")
            if totals['legacy']:
                self.stdout.write(
                    f"NOTE: {totals['legacy']} credential(s) still use the legacy XOR "
                    "obfuscation. They work, but re-save them in the admin UI to move "
                    "them onto Fernet; this is not a failure and exits 0."
                )

        if totals['unreadable']:
            if not options['json']:
                self.stderr.write(
                    f"{totals['unreadable']} stored credential(s) cannot be decrypted "
                    "under the current DJANGO_FIELD_ENCRYPTION_KEY; users see an empty "
                    "password and what looks like an IMAP/SMTP outage. Re-enter them in "
                    "the admin UI to re-encrypt under the current key."
                )
            # Non-zero for CI / cron alerting. Not a CommandError: that would print
            # a traceback-style message and is reserved for a malformed invocation.
            raise SystemExit(1)
