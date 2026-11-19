"""Audio encoding helpers (ffmpeg subprocess) for multimodal LLM payloads.

Direct port of helpdesk/backend/app/utils/audio_utils.py. Operates on a real
filesystem path: the orchestrator materializes a private-storage upload into a
temporary file before calling these, so no ``.path`` / MEDIA_ROOT access leaks in.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path


def convert_to_mp3(input_path: str) -> str:
    """Convert any audio file to mp3 using ffmpeg; return the temp mp3 path."""
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f'{Path(input_path).stem}_{os.getpid()}.mp3')

    try:
        # -y overwrite, libmp3lame encoder, -q:a 2 ~ 190 kbps VBR.
        subprocess.run(
            ['ffmpeg', '-y', '-i', input_path, '-codec:a', 'libmp3lame', '-q:a', '2', output_path],
            check=True,
            capture_output=True,
        )
        return output_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg conversion failed: {e.stderr.decode()}')


def encode_audio_as_base64(path: str) -> str:
    """Convert audio to mp3 (if possible) and encode as ``data:audio/mpeg;base64,...``."""
    temp_mp3 = None

    try:
        temp_mp3 = convert_to_mp3(path)
        final_path = temp_mp3
        mime_type = 'audio/mpeg'
    except Exception:
        # Fall back to the original bytes if conversion fails.
        final_path = str(path)
        mime_type, _ = mimetypes.guess_type(final_path)
        if not mime_type:
            mime_type = 'audio/mpeg'

    try:
        b64 = base64.b64encode(Path(final_path).read_bytes()).decode('ascii')
        return f'data:{mime_type};base64,{b64}'
    finally:
        if temp_mp3 and os.path.exists(temp_mp3):
            try:
                os.remove(temp_mp3)
            except Exception:
                pass