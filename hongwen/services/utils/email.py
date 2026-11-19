"""Password-reset email delivery over plain SMTP (stdlib smtplib).

Port of helpdesk/backend/app/utils/email.py. Config comes from Django settings
(``SMTP_*``, read from env in waypost/settings.py) instead of os.getenv. The
brand signature is 泓问 (the helpdesk used helpdesk-mini).

Import aliased -- ``from hongwen.services.utils import email as email_utils`` --
because the module shares its name with the stdlib ``email`` package; the
absolute ``from email.message import ...`` below still binds the stdlib one.

This is independent of Waypost's per-mailbox DatabaseSMTPEmailBackend: it is the
hongwen customer reset relay, fail-closed by design (an undelivered code never
starts a cooldown).
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# Pragmatic rather than RFC 5322: one @, no whitespace, a dotted domain with a
# real TLD, and the length ceilings SMTP relays enforce.
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$')
_LOCAL_PART_MAX = 64
_ADDRESS_MAX = 254
_TLD_MIN = 2

_SSL_PORT = 465
_STARTTLS_PORT = 587
_VALID_SECURITY = ('auto', 'ssl', 'starttls', 'none')

# Brand signature for hongwen reset mail (subject prefix + body sign-off).
EMAIL_BRAND = '泓问'


def _text(name: str) -> str:
    return str(getattr(settings, name, '') or '').strip()


def _flag(name: str, *, default: bool) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    raw = str(value or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on'}


def is_email_address(candidate: Optional[str]) -> bool:
    """True when ``candidate`` is shaped like a mailbox that could receive a code."""
    if not isinstance(candidate, str):
        return False
    address = candidate.strip()
    if not address or len(address) > _ADDRESS_MAX:
        return False
    if not _EMAIL_RE.match(address):
        return False
    local_part, _, domain = address.rpartition('@')
    if not local_part or len(local_part) > _LOCAL_PART_MAX:
        return False
    return len(domain.rsplit('.', 1)[-1]) >= _TLD_MIN


def is_enabled() -> bool:
    return _flag('PASSWORD_RESET_EMAIL_ENABLED', default=True)


def is_configured() -> bool:
    """A relay with no host cannot be talked to; one with no password cannot auth."""
    return bool(_text('SMTP_HOST')) and bool(_text('SMTP_PASSWORD'))


def _port() -> int:
    raw = _text('SMTP_PORT')
    if not raw:
        return _SSL_PORT
    try:
        return int(raw)
    except ValueError:
        return _SSL_PORT


def _security(port: int) -> str:
    configured = _text('SMTP_SECURITY').lower()
    if configured not in _VALID_SECURITY:
        configured = 'auto'
    if configured != 'auto':
        return configured
    if port == _SSL_PORT:
        return 'ssl'
    if port == _STARTTLS_PORT:
        return 'starttls'
    return 'none'


def _timeout_seconds() -> float:
    raw = _text('SMTP_TIMEOUT_SECONDS')
    try:
        return max(float(raw or '10'), 1.0)
    except ValueError:
        return 10.0


def _from_address() -> str:
    return _text('SMTP_FROM') or _text('SMTP_USERNAME')


def build_email(*, to_addr: str, subject: str, text_body: str, html_body: Optional[str] = None) -> EmailMessage:
    """Assemble the message. Date and Message-ID are filled in by ``send_email``."""
    message = EmailMessage()
    message['From'] = _from_address()
    message['To'] = to_addr
    message['Subject'] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype='html')
    return message


def build_password_reset_email(*, to_addr: str, code: str, minutes: int) -> EmailMessage:
    """Branded 泓问 reset-code mail (subject prefix + sign-off carry the brand)."""
    subject = str(_('【泓问】密码重置验证码'))
    text_body = str(_(
        '您的密码重置验证码是 {code}，{minutes} 分钟内有效。如非本人操作，请忽略此邮件。\n\n—— 泓问'
    )).format(code=code, minutes=minutes)
    return build_email(to_addr=to_addr, subject=subject, text_body=text_body)


def _send(message: EmailMessage) -> None:
    """The single place this module touches the network, so tests can stub it wholesale."""
    host = _text('SMTP_HOST')
    port = _port()
    security = _security(port)
    username = _text('SMTP_USERNAME')
    password = _text('SMTP_PASSWORD')

    context = ssl.create_default_context() if security in {'ssl', 'starttls'} else None
    smtp: Optional[smtplib.SMTP] = None
    try:
        if security == 'ssl':
            smtp = smtplib.SMTP_SSL(host, port, timeout=_timeout_seconds(), context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=_timeout_seconds())
        if security == 'starttls':
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                # A failed goodbye says nothing about acceptance; do not mask the real error.
                pass


def send_email(message: EmailMessage) -> bool:
    """Deliver one message, reporting success. Never raises into the caller.

    Fail-closed: a reset code that was never sent has no degraded mode that
    preserves security, so the caller answers generically and writes no code row.
    The code itself is never logged, at any level.
    """
    if not is_enabled() or not is_configured():
        logger.warning('Password reset email skipped: SMTP is not configured')
        return False
    try:
        _send(message)
        return True
    except Exception as exc:
        logger.error('SMTP delivery to %s failed: %s', message['To'], exc)
        return False