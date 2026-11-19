"""Triage screenshot assets, shipped in-package and materialized to private storage.

Port of helpdesk/backend/app/triage/assets.py. The packaged PNGs live in
``hongwen/services/triage/seed_assets/`` (an app runtime asset, allowed by the
hygiene guard) and are copied into private storage ``hongwen/triage_assets/`` by
``materialize_seed_assets`` so a fresh container has them. An admin who replaced
a screenshot wins over the shipped default (never overwrite). When a file is
missing, ``render_step`` degrades to text-only instructions.
"""
from __future__ import annotations

import os
from typing import List, Optional

from django.core.files.base import ContentFile

from hongwen.services.storage import private_storage
from hongwen.services.utils.file_store import build_protected_upload_url, storage_name_for_relative_path

TRIAGE_ASSET_SUBDIR = 'triage_assets'
SEED_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seed_assets')


def materialize_seed_assets() -> List[str]:
    """Copy packaged screenshots into private storage; return what was copied."""
    if not os.path.isdir(SEED_ASSET_DIR):
        return []
    storage = private_storage()
    copied: List[str] = []
    for name in sorted(os.listdir(SEED_ASSET_DIR)):
        if not name.lower().endswith('.png'):
            continue
        storage_name = f'hongwen/{TRIAGE_ASSET_SUBDIR}/{name}'
        if storage.exists(storage_name):
            continue
        with open(os.path.join(SEED_ASSET_DIR, name), 'rb') as fh:
            storage.save(storage_name, ContentFile(fh.read()))
        copied.append(name)
    return copied


def asset_relative_path(filename: str) -> str:
    """Relative path (under the storage root) for a screenshot filename."""
    return f'{TRIAGE_ASSET_SUBDIR}/{os.path.basename(filename)}'


def resolve_asset_url(relative_path: str) -> str:
    """Protected download URL for a stored screenshot."""
    return build_protected_upload_url(relative_path)


def asset_exists(relative_path: str) -> bool:
    if not relative_path:
        return False
    return private_storage().exists(storage_name_for_relative_path(relative_path))


def read_asset_dimensions(relative_path: str) -> Optional[tuple[int, int]]:
    """Best-effort (width, height); None if missing or unreadable."""
    if not asset_exists(relative_path):
        return None
    try:
        from PIL import Image

        with private_storage().open(storage_name_for_relative_path(relative_path)) as fh:
            with Image.open(fh) as image:
                return int(image.width), int(image.height)
    except Exception:
        return None