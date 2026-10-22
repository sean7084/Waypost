"""
Field-level encryption for sensitive credentials stored in the database.

Historically, mailbox/SMTP passwords were "protected" with a reversible XOR
obfuscation keyed on ``SECRET_KEY`` (the old ``_xor_secret`` helper). That is
NOT encryption: anyone with ``SECRET_KEY`` (or a DB dump plus the key) can
recover the plaintext trivially.

This module replaces it with **Fernet** (AES-128-CBC + HMAC-SHA256 authenticated
symmetric encryption) from the ``cryptography`` package.

Key management
--------------
- Production MUST set ``DJANGO_FIELD_ENCRYPTION_KEY`` to a valid Fernet key.
  Generate one with::

      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

- Development may omit it; a stable key is then derived from ``SECRET_KEY`` so
  the app works out of the box. ``settings.py`` enforces an explicit key when
  ``DEBUG=False`` (fail-fast), mirroring the ``DJANGO_SECRET_KEY`` guard.

Backward compatibility
----------------------
- :func:`decrypt_secret` transparently falls back to the legacy XOR scheme for
  any value that has not yet been re-encrypted, so the transition is seamless.
- Data migration ``accounts/migrations/0019_*`` re-encrypts existing values.
- A value that decrypts under neither scheme returns ``''`` (never raises) and
  logs a WARNING so the failure is visible in the application log; run
  ``manage.py check_encrypted_secrets`` to audit every stored credential at once.

Key rotation is out of scope: changing ``DJANGO_FIELD_ENCRYPTION_KEY`` after
data exists requires a re-encryption migration (decrypt with the old key,
encrypt with the new one).
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

LOGGER = logging.getLogger(__name__)


def _resolve_key():
    """Return the Fernet key bytes.

    Uses the explicit ``FIELD_ENCRYPTION_KEY`` setting when present; otherwise
    derives a stable 32-byte url-safe key from ``SECRET_KEY`` (development only).
    """
    key = (getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or '').strip()
    if key:
        return key.encode('utf-8')
    digest = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    return Fernet(_resolve_key())


def encrypt_secret(plaintext):
    """Encrypt a plaintext secret; returns a Fernet token string ('' for empty input)."""
    if not plaintext:
        return ''
    return _fernet().encrypt(plaintext.encode('utf-8')).decode('ascii')


def decrypt_secret(token, context=None):
    """Decrypt a stored secret.

    Tries Fernet first, then the legacy XOR scheme for values that predate the
    migration. Returns '' for empty or undecryptable input (never raises).

    ``context`` is an optional operator-facing label for the row being decrypted
    (e.g. a mailbox pk and its host). It is only ever used in the log line, so it
    must contain NO secret material: never pass the token, the plaintext or a key.

    The silent '' return is deliberate - every caller treats an unreadable
    credential as "not configured" and raising would break mailbox sync - but it
    must not be silent in the log. A token that cannot be decrypted under the
    current ``FIELD_ENCRYPTION_KEY`` (a rotated key, or a corrupted value) is
    indistinguishable from an empty one at the call site, so the user only sees an
    auth failure that looks exactly like an IMAP outage. The warning names that
    symptom, and says the fix is for the operator to re-enter the credential in
    the UI (which re-encrypts it under the current key).
    """
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, TypeError):
        pass
    try:
        return _legacy_xor_decrypt(token)
    except Exception:
        LOGGER.warning(
            'Stored credential for %s could not be decrypted, so it is being '
            'reported as unset; the user will see an empty password and what '
            'looks like an IMAP/SMTP auth failure. Re-enter the credential in the '
            'admin UI to re-encrypt it under the current '
            'DJANGO_FIELD_ENCRYPTION_KEY.',
            context or 'an unknown mailbox/SMTP row',
        )
        return ''


def is_fernet_token(value):
    """True if ``value`` is already a valid Fernet token under the current key.

    Used by the data migration to stay idempotent (skip already-migrated rows).
    """
    if not value:
        return False
    try:
        _fernet().decrypt(value.encode('ascii'))
        return True
    except Exception:
        return False


def secret_scheme(value):
    """Return which scheme can read ``value``: ``'fernet'``, ``'legacy'`` or None.

    ``None`` means :func:`decrypt_secret` will return '' for it, i.e. the row is
    as good as unset for the user. ``'legacy'`` *does* read back, but it is the
    pre-Fernet XOR obfuscation and should be re-encrypted.

    This is the audit-side twin of ``decrypt_secret``'s try-Fernet-then-XOR order;
    it answers the two cases separately, which the call site cannot because both
    failures and legacy hits are indistinguishable from '' once returned.
    """
    if not value:
        return None
    if is_fernet_token(value):
        return 'fernet'
    try:
        _legacy_xor_decrypt(value)
    except Exception:
        return None
    return 'legacy'


def _legacy_xor_decrypt(value):
    """Decrypt a value produced by the old ``_xor_secret`` (SECRET_KEY-based XOR).

    Retained only to migrate/decrypt pre-existing data. Do NOT use for new writes.
    """
    key = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    raw = base64.urlsafe_b64decode(value.encode('ascii'))
    restored = bytes(raw[index] ^ key[index % len(key)] for index in range(len(raw)))
    return restored.decode('utf-8')
