"""Image encoding helpers for OpenAI-style vision payloads.

Direct port of helpdesk/backend/app/utils/image_utils.py (path-based; the
orchestrator materializes private-storage uploads to a temp file first).
"""
from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import Tuple

from PIL import Image


def encode_image_as_data_url(path: str) -> Tuple[str, str]:
    """Return (mime_type, data_url). Non-JPEG/PNG images are converted to JPEG."""
    path_obj = Path(path)
    mime_type, _ = mimetypes.guess_type(path)

    supported_mimes = ['image/jpeg', 'image/png', 'image/jpg']

    if mime_type not in supported_mimes:
        with Image.open(path) as img:
            # JPEG has no alpha channel.
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG')
            b64 = base64.b64encode(buffer.getvalue()).decode('ascii')
            mime_type = 'image/jpeg'
    else:
        b64 = base64.b64encode(path_obj.read_bytes()).decode('ascii')
        if mime_type == 'image/jpg':
            mime_type = 'image/jpeg'

    return mime_type, f'data:{mime_type};base64,{b64}'