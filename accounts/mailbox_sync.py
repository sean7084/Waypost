import email
import imaplib
import logging
import poplib
import threading
import time
from datetime import timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from django.db import close_old_connections
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from .models import ReceivedEmailMessage, UserMailboxSettings


LOGGER = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = 300
INITIAL_SYNC_DELAY_SECONDS = 5
_SYNC_THREAD_STARTED = False
_SYNC_LOCKS = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def _get_mailbox_lock(mailbox_settings):
    key = mailbox_settings.pk or id(mailbox_settings)
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[key] = lock
    return lock


def _decode_header_value(value):
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_message_body(message_obj):
    if message_obj.is_multipart():
        for part in message_obj.walk():
            content_type = part.get_content_type()
            disposition = (part.get('Content-Disposition') or '').lower()
            if content_type == 'text/plain' and 'attachment' not in disposition:
                payload = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
        return ''
    payload = message_obj.get_payload(decode=True) or b''
    charset = message_obj.get_content_charset() or 'utf-8'
    return payload.decode(charset, errors='replace')


def _normalize_datetime(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value) if isinstance(value, str) else value
    except Exception:
        return None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(timezone.get_current_timezone())


def _lookback_start(mailbox_settings):
    months = max(1, mailbox_settings.sync_lookback_months or 6)
    return timezone.now() - timedelta(days=months * 30)


def _build_defaults(mailbox_settings, parsed_message, body_text, direction, folder_name, message_dt, protocol, extra_metadata=None):
    metadata = {'protocol': protocol}
    # The machine-generated markers are copied verbatim because accounts/rfq_ai.py
    # reads them to decide whether a mail is worth an LLM call: a header the
    # sending server wrote about itself beats any match against the body text.
    for header in ('Auto-Submitted', 'X-Autoreply', 'List-Unsubscribe'):
        value = parsed_message.get(header)
        if value:
            metadata[header] = value
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        'message_id': parsed_message.get('Message-ID', ''),
        'folder_name': folder_name,
        'subject': _decode_header_value(parsed_message.get('Subject', '')),
        'sender': _decode_header_value(parsed_message.get('From', '')),
        'recipients': parsed_message.get('To', ''),
        'received_at': message_dt if direction == ReceivedEmailMessage.MessageDirection.INBOX else None,
        'sent_at': message_dt if direction == ReceivedEmailMessage.MessageDirection.OUTBOX else None,
        'body_preview': body_text[:500],
        'body_text': body_text,
        'metadata': metadata,
    }


def _sync_imap_folder(client, mailbox_settings, folder_name, direction, since_dt):
    synced_count = 0
    status, _data = client.select(f'"{folder_name}"')
    if status != 'OK':
        return 0

    criteria = ['SINCE', since_dt.strftime('%d-%b-%Y')]
    status, data = client.search(None, *criteria)
    if status != 'OK' or not data or not data[0]:
        return 0

    for external_id in reversed(data[0].split()):
        fetch_status, message_data = client.fetch(external_id, '(RFC822)')
        if fetch_status != 'OK' or not message_data:
            continue
        raw_message = next((part[1] for part in message_data if isinstance(part, tuple)), None)
        if not raw_message:
            continue
        parsed_message = email.message_from_bytes(raw_message)
        message_dt = _normalize_datetime(parsed_message.get('Date'))
        if message_dt and message_dt < since_dt:
            continue
        body_text = _extract_message_body(parsed_message)
        uid = external_id.decode('ascii', errors='ignore')
        ReceivedEmailMessage.objects.update_or_create(
            mailbox=mailbox_settings,
            direction=direction,
            external_id=f'{folder_name}:{uid}',
            defaults=_build_defaults(
                mailbox_settings,
                parsed_message,
                body_text,
                direction,
                folder_name,
                message_dt,
                'imap',
                {'uid': uid},
            ),
        )
        synced_count += 1
    return synced_count


def _sync_pop3_inbox(mailbox_settings, since_dt):
    if mailbox_settings.pop3_security == UserMailboxSettings.ConnectionSecurity.SSL_TLS:
        client = poplib.POP3_SSL(mailbox_settings.pop3_host, mailbox_settings.pop3_port)
    else:
        client = poplib.POP3(mailbox_settings.pop3_host, mailbox_settings.pop3_port)
        if mailbox_settings.pop3_security == UserMailboxSettings.ConnectionSecurity.STARTTLS and hasattr(client, 'stls'):
            client.stls()

    synced_count = 0
    try:
        client.user(mailbox_settings.username)
        client.pass_(mailbox_settings.password)
        message_total = len(client.list()[1])
        for message_number in range(message_total, 0, -1):
            _response, lines, _octets = client.retr(message_number)
            raw_message = b'\n'.join(lines)
            parsed_message = email.message_from_bytes(raw_message)
            message_dt = _normalize_datetime(parsed_message.get('Date'))
            if message_dt and message_dt < since_dt:
                continue
            body_text = _extract_message_body(parsed_message)
            external_id = parsed_message.get('Message-ID') or f'pop3-{message_number}'
            ReceivedEmailMessage.objects.update_or_create(
                mailbox=mailbox_settings,
                direction=ReceivedEmailMessage.MessageDirection.INBOX,
                external_id=external_id,
                defaults=_build_defaults(
                    mailbox_settings,
                    parsed_message,
                    body_text,
                    ReceivedEmailMessage.MessageDirection.INBOX,
                    'INBOX',
                    message_dt,
                    'pop3',
                    {'message_number': message_number},
                ),
            )
            synced_count += 1
    finally:
        client.quit()
    return synced_count


def sync_mailbox_messages(mailbox_settings):
    lock = _get_mailbox_lock(mailbox_settings)
    with lock:
        return _sync_mailbox_messages_unlocked(mailbox_settings)


def _sync_mailbox_messages_unlocked(mailbox_settings):
    password = mailbox_settings.password
    if not password:
        raise ValueError('Mailbox password is not configured.')

    since_dt = _lookback_start(mailbox_settings)
    synced_count = {'inbox': 0, 'outbox': 0}
    if mailbox_settings.receive_protocol == UserMailboxSettings.ReceiveProtocol.IMAP:
        if mailbox_settings.imap_security == UserMailboxSettings.ConnectionSecurity.SSL_TLS:
            client = imaplib.IMAP4_SSL(mailbox_settings.imap_host, mailbox_settings.imap_port)
        else:
            client = imaplib.IMAP4(mailbox_settings.imap_host, mailbox_settings.imap_port)
            if mailbox_settings.imap_security == UserMailboxSettings.ConnectionSecurity.STARTTLS:
                client.starttls()
        try:
            client.login(mailbox_settings.username, password)
            synced_count['inbox'] = _sync_imap_folder(
                client,
                mailbox_settings,
                'INBOX',
                ReceivedEmailMessage.MessageDirection.INBOX,
                since_dt,
            )
            if mailbox_settings.sync_outbox:
                synced_count['outbox'] = _sync_imap_folder(
                    client,
                    mailbox_settings,
                    mailbox_settings.imap_sent_folder or 'Sent',
                    ReceivedEmailMessage.MessageDirection.OUTBOX,
                    since_dt,
                )
        finally:
            client.logout()
    else:
        synced_count['inbox'] = _sync_pop3_inbox(mailbox_settings, since_dt)

    mailbox_settings.last_mailbox_sync_at = timezone.now()
    mailbox_settings.last_connection_test_at = timezone.now()
    mailbox_settings.last_connection_status = 'success'
    mailbox_settings.last_connection_message = 'Mailbox synchronized successfully.'
    mailbox_settings.save(update_fields=['last_mailbox_sync_at', 'last_connection_test_at', 'last_connection_status', 'last_connection_message', 'updated_at'])
    from .rfq_ai import process_pending_rfq_messages

    process_pending_rfq_messages(mailbox_settings)
    return synced_count


def maybe_auto_sync_mailbox(mailbox_settings):
    """Sync one mailbox if it is due, or return False without touching it.

    Returns the per-folder message counts when it did sync, which is what the
    `sync_mailboxes` command reports. Callers that only care about "did it run"
    can test truthiness: the dict is never empty.
    """
    if not mailbox_settings or not mailbox_settings.is_active or not mailbox_settings.auto_sync_enabled:
        return False
    if mailbox_settings.last_mailbox_sync_at and timezone.now() - mailbox_settings.last_mailbox_sync_at < timedelta(seconds=SYNC_INTERVAL_SECONDS):
        return False
    lock = _get_mailbox_lock(mailbox_settings)
    if not lock.acquire(blocking=False):
        return False
    try:
        return _sync_mailbox_messages_unlocked(mailbox_settings)
    finally:
        lock.release()


def cache_dispatch_outbox_message(dispatch, mailbox_settings):
    sent_at = dispatch.sent_at or timezone.now()
    ReceivedEmailMessage.objects.update_or_create(
        mailbox=mailbox_settings,
        direction=ReceivedEmailMessage.MessageDirection.OUTBOX,
        external_id=f'dispatch:{dispatch.pk}',
        defaults={
            'message_id': '',
            'folder_name': mailbox_settings.imap_sent_folder or 'Sent',
            'subject': dispatch.subject,
            'sender': mailbox_settings.email_address,
            'recipients': ', '.join(filter(None, [dispatch.sent_to, dispatch.cc, dispatch.bcc])),
            'received_at': None,
            'sent_at': sent_at,
            'body_preview': (dispatch.body or '')[:500],
            'body_text': dispatch.body or '',
            'metadata': {'protocol': 'smtp', 'dispatch_id': dispatch.pk},
        },
    )


def sync_all_active_mailboxes(deadline=None):
    """One pass over every auto-sync mailbox. Returns (mailboxes, messages) synced.

    `deadline` is a `time.monotonic()` value: a timer tick must not outlive the
    interval that scheduled it, so the pass stops **between** mailboxes rather
    than mid-request. A mailbox it never reached is picked up by the next pass,
    which is why the queue is ordered by primary key instead of by staleness -
    ordering by last sync would starve the same mailbox every time.
    """
    close_old_connections()
    synced_mailboxes = 0
    synced_messages = 0
    try:
        mailboxes = UserMailboxSettings.objects.filter(is_active=True, auto_sync_enabled=True).order_by('pk')
        for mailbox_settings in mailboxes:
            if deadline is not None and time.monotonic() >= deadline:
                LOGGER.warning('Mailbox sync hit its runtime limit with mailboxes still queued.')
                break
            try:
                result = maybe_auto_sync_mailbox(mailbox_settings)
            except Exception as exc:
                mailbox_settings.last_connection_test_at = timezone.now()
                mailbox_settings.last_connection_status = 'failed'
                mailbox_settings.last_connection_message = str(exc)
                mailbox_settings.save(update_fields=['last_connection_test_at', 'last_connection_status', 'last_connection_message', 'updated_at'])
                LOGGER.warning('Mailbox auto-sync failed for %s: %s', mailbox_settings.pk, exc)
                continue
            if result:
                synced_mailboxes += 1
                synced_messages += sum(result.values())
    except (OperationalError, ProgrammingError):
        LOGGER.debug('Mailbox auto-sync skipped before database is ready.')
    finally:
        close_old_connections()
    return synced_mailboxes, synced_messages


def start_mailbox_sync_thread():
    global _SYNC_THREAD_STARTED
    if _SYNC_THREAD_STARTED:
        return

    def _run_loop():
        # Avoid database access during Django app initialization.
        time.sleep(INITIAL_SYNC_DELAY_SECONDS)
        while True:
            sync_all_active_mailboxes()
            time.sleep(SYNC_INTERVAL_SECONDS)

    thread = threading.Thread(target=_run_loop, name='mailbox-auto-sync', daemon=True)
    thread.start()
    _SYNC_THREAD_STARTED = True