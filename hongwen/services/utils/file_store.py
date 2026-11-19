"""Private-storage file helpers for Hongwen uploads, KB attachments and TTS audio.

Rewrite of helpdesk/backend/app/utils/file_store.py onto Django's storage API.
The helpdesk wrote straight to an ``uploads/`` directory on disk; here every
customer-facing blob lives under ``HONGWEN_PRIVATE_MEDIA_ROOT`` through
``hongwen.services.storage.private_storage`` and is reached only via
``storage.save`` / ``open`` / ``exists`` / ``delete`` - never ``.path`` or
``MEDIA_ROOT`` (nginx serves the public media root world-readable).

Path convention (PR4's protected-download view relies on it):
  * a *relative path* is the storage name minus the leading ``hongwen/`` segment,
    e.g. ``uploads/<file_id>__<name>``, ``kb_assets/<uuid>.png``,
    ``triage_assets/<name>.png``, ``tts/<uuid>.mp3``;
  * ``build_protected_upload_url`` turns that into ``/api/uploads/<...>`` (the
    ``uploads/`` segment is collapsed, matching the helpdesk client URLs);
  * ``storage_name_for_relative_path`` is the inverse mapping PR4 uses to open
    the blob behind a requested URL.

KB *source documents* are different: they are operator-managed files under
``HONGWEN_KB_SOURCES_DIR`` (see kb.paths), read with plain ``open()`` and cleaned
up with ``delete_file_if_exists`` - they are not media and never touch storage.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from django.core.files.base import ContentFile

from hongwen.models import UploadFile
from hongwen.services.storage import private_storage

UPLOAD_API_PREFIX = '/api/uploads'

# Subdirectories (under the hongwen/ storage prefix) that are NOT customer uploads.
_NON_UPLOAD_SUBDIRS = ('kb_assets/', 'triage_assets/', 'tts/')


def kb_document_display_name(path: str) -> str:
    """Strip the generated 32-hex storage prefix from a KB source filename."""
    base_name = os.path.basename(path)
    prefix, separator, remainder = base_name.partition('_')
    if separator and len(prefix) == 32 and all(ch in '0123456789abcdef' for ch in prefix.lower()) and remainder:
        return remainder
    return base_name


def build_attachment_caption(source: str, page_or_section: str) -> str:
    """The caption both chat clients render for a KB attachment (live + history)."""
    parts = [
        kb_document_display_name(str(source or '')),
        str(page_or_section or '').strip(),
    ]
    return ' · '.join(part for part in parts if part)


def normalize_upload_relative_path(path: str) -> str:
    normalized = str(path or '').strip().replace('\\', '/')
    for prefix in (
        f'{UPLOAD_API_PREFIX}/',
        f"{UPLOAD_API_PREFIX.lstrip('/')}/",
        '/uploads/',
        'uploads/',
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized.lstrip('/')


def build_protected_upload_url(relative_path: str) -> str:
    normalized = normalize_upload_relative_path(relative_path)
    if not normalized:
        return UPLOAD_API_PREFIX
    return f'{UPLOAD_API_PREFIX}/{quote(normalized, safe="/")}'


def storage_name_for_relative_path(relative_path: str) -> str:
    """Map a client-facing relative path to its private-storage name.

    Inverse of the URL convention above: a bare filename is a customer upload
    (``hongwen/uploads/...``); a known asset subdirectory keeps its segment
    (``hongwen/kb_assets/...`` etc.). PR4's download view calls this.
    """
    clean = str(relative_path or '').strip().replace('\\', '/').lstrip('/')
    for prefix in ('api/uploads/', 'uploads/'):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    if clean.startswith(_NON_UPLOAD_SUBDIRS):
        return f'hongwen/{clean}'
    return f'hongwen/uploads/{clean}'


def store_upload_bytes(
    *,
    user,
    filename: str,
    data: bytes,
    file_type: str,
    mime_type: str = 'application/octet-stream',
    duration_seconds: Optional[int] = None,
) -> UploadFile:
    """Persist a customer upload through private storage and record the row.

    Returns the ``UploadFile`` (``file_id`` is the client-facing uuid4 hex).
    """
    safe_name = os.path.basename(filename)
    file_id = uuid.uuid4().hex
    # Save through a fresh private_storage() (current settings) rather than
    # FieldFile.save: the model field caches its resolved storage instance, so
    # going through the field would pin the first test's root. Assigning the
    # returned name records the blob without re-saving.
    storage = private_storage()
    name = storage.save(f'hongwen/uploads/{file_id}__{safe_name}', ContentFile(data))
    upload = UploadFile.objects.create(
        user=user,
        file_id=file_id,
        file_type=file_type,
        file=name,
        original_filename=safe_name,
        mime_type=mime_type,
        duration_seconds=duration_seconds,
    )
    return upload


def open_upload(relative_path: str):
    """Open a stored blob by relative path through the storage API (file-like)."""
    return private_storage().open(storage_name_for_relative_path(relative_path))


def upload_exists(relative_path: str) -> bool:
    return private_storage().exists(storage_name_for_relative_path(relative_path))


def delete_upload(relative_path: str) -> None:
    storage = private_storage()
    name = storage_name_for_relative_path(relative_path)
    if storage.exists(name):
        storage.delete(name)


def save_kb_asset_bytes(data: bytes, ext: str, *, subdir: str = 'kb_assets') -> str:
    """Store a KB/triage image blob and return its relative path (``<subdir>/<uuid>.<ext>``)."""
    filename = f'{uuid.uuid4().hex}.{ext}'
    storage = private_storage()
    storage.save(f'hongwen/{subdir}/{filename}', ContentFile(data))
    return f'{subdir}/{filename}'


def materialize_upload_to_tempfile(upload: UploadFile) -> str:
    """Copy a private-storage upload to a real temp file for ffmpeg / PyMuPDF.

    The media encoding helpers subprocess out to ffmpeg, which needs an actual
    filesystem path; this bridges storage -> temp file without ever resolving
    ``.path``. The caller owns the returned path and must delete it.
    """
    suffix = os.path.splitext(upload.file.name)[1] or ''
    with private_storage().open(upload.file.name) as src:
        data = src.read()
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
    return tmp_path


def delete_file_if_exists(path: str) -> None:
    """Remove a KB *source* file (under HONGWEN_KB_SOURCES_DIR), tolerating absence.

    The database is the source of truth; manual cleanup may already have removed
    the artifact, so a missing file must not fail a delete/replace request.
    """
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except IsADirectoryError as exc:
        raise ValueError(f'Expected a file path but received a directory: {path}') from exc