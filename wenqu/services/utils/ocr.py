"""Optional OCR for KB-embedded images (PDF/DOCX ingestion).

Direct port of helpdesk/backend/app/utils/ocr.py. Requires the system
``tesseract`` binary plus ``pytesseract`` + ``Pillow``; when any is missing the
helper returns an empty string so ingestion never fails on OCR.
"""
from __future__ import annotations


def ocr_image_bytes(image_bytes: bytes) -> str:
    try:
        import io

        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        # Tesseract performs better on grayscale.
        img = img.convert('L')
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception:
        # OCR is optional; failure must not stop ingestion.
        return ''