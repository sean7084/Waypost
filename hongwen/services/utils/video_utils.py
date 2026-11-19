"""Video encoding helpers (ffmpeg subprocess) for multimodal LLM payloads.

Direct port of helpdesk/backend/app/utils/video_utils.py (path-based; the
orchestrator materializes private-storage uploads to a temp file first).
"""
from __future__ import annotations

import base64
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path


def convert_to_mp4(input_path: str) -> str:
    """Convert any video file to mp4 using ffmpeg; return the temp mp4 path."""
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f'{Path(input_path).stem}_{os.getpid()}.mp4')

    try:
        # h264 + aac, yuv420p for broad player/model compatibility.
        subprocess.run(
            ['ffmpeg', '-y', '-i', input_path, '-c:v', 'libx264', '-c:a', 'aac',
             '-pix_fmt', 'yuv420p', '-crf', '23', output_path],
            check=True,
            capture_output=True,
        )
        return output_path
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'FFmpeg video conversion failed: {e.stderr.decode()}')


def encode_video_as_base64(path: str) -> str:
    """Convert video to mp4 and encode as ``data:video/mp4;base64,...``."""
    temp_mp4 = None

    try:
        temp_mp4 = convert_to_mp4(path)
        final_path = temp_mp4
        mime_type = 'video/mp4'
    except Exception:
        final_path = str(path)
        mime_type, _ = mimetypes.guess_type(final_path)
        if not mime_type:
            mime_type = 'video/mp4'

    try:
        b64 = base64.b64encode(Path(final_path).read_bytes()).decode('utf-8')
        return f'data:{mime_type};base64,{b64}'
    finally:
        if temp_mp4 and os.path.exists(temp_mp4):
            try:
                os.remove(temp_mp4)
            except Exception:
                pass