"""Where a knowledge-base source file lives on disk.

Port of helpdesk/backend/app/kb/paths.py. The KBDocument path column and
``KBChunk.source_path`` store the path **relative to the kb_sources root**
(``WENQU_KB_SOURCES_DIR``), so a row survives the project directory moving.
Legacy rows that hold absolute paths from another machine are still found by
their final component. These are operator-managed source files, read with plain
``open()`` - not media, so they never touch the storage API.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from django.conf import settings

SOURCES_SUBDIR = 'kb_sources'
_DRIVE_PREFIX = re.compile(r'^[A-Za-z]:')


def sources_root() -> str:
    """Absolute kb_sources directory (``WENQU_KB_SOURCES_DIR``)."""
    base = str(getattr(settings, 'WENQU_KB_SOURCES_DIR', '') or '').strip()
    if not base:
        base = os.path.join(os.getcwd(), SOURCES_SUBDIR)
    return os.path.abspath(base)


def ensure_sources_root() -> str:
    root = sources_root()
    os.makedirs(root, exist_ok=True)
    return root


def to_stored_path(value: str) -> str:
    """Collapse any accepted spelling down to the kb_sources-relative form."""
    normalized = str(value or '').strip().replace('\\', '/')
    if not normalized:
        return ''

    marker = f'/{SOURCES_SUBDIR}/'
    index = normalized.rfind(marker)
    if index >= 0:
        return normalized[index + len(marker):]

    plain_prefix = f'{SOURCES_SUBDIR}/'
    if normalized.startswith(plain_prefix):
        return normalized[len(plain_prefix):]

    # os.path.isabs is platform-dependent; legacy rows mix Windows drive paths
    # with POSIX ones regardless of the running platform.
    looks_absolute = normalized.startswith(('/', '\\\\')) or bool(_DRIVE_PREFIX.match(normalized))
    if looks_absolute:
        return normalized.rsplit('/', 1)[-1]
    return normalized.lstrip('/')


def resolve_stored_path(value: str) -> str:
    """Absolute path for a stored value. The file may not exist; nothing raises."""
    stored = to_stored_path(value)
    if not stored:
        return ''
    candidate = os.path.join(sources_root(), *stored.split('/'))
    if os.path.exists(candidate):
        return candidate

    literal = str(value or '').strip()
    if literal and os.path.exists(literal):
        return literal
    return candidate


def stored_path_exists(value: str) -> bool:
    return bool(to_stored_path(value)) and os.path.isfile(resolve_stored_path(value))


def missing_stored_paths(paths: Optional[list[str]]) -> list[str]:
    return sorted({path for path in (paths or []) if not stored_path_exists(path)})