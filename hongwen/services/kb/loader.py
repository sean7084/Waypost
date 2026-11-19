"""KB source-document loading: PDF (PyMuPDF), DOCX (python-docx), TXT, with OCR.

Port of helpdesk/backend/app/kb/loader.py. Source files are read with plain
``open()`` from ``HONGWEN_KB_SOURCES_DIR`` (operator-managed, not media). Extracted
images are persisted through private storage (``save_kb_asset_bytes``) rather
than the helpdesk's public ``uploads/kb_assets`` directory.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal, Tuple

from hongwen.services.utils.file_store import save_kb_asset_bytes


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def detect_source_type(path: str) -> Literal['pdf', 'docx', 'txt']:
    lower = path.lower()
    if lower.endswith('.pdf'):
        return 'pdf'
    if lower.endswith('.docx') or lower.endswith('.doc'):
        return 'docx'
    if lower.endswith('.txt'):
        return 'txt'
    raise ValueError(f'Unsupported knowledge base file type: {path}')


def save_kb_image(img_bytes: bytes, ext: str) -> str:
    """Save a KB-extracted image to private storage; return its relative path."""
    return save_kb_asset_bytes(img_bytes, ext, subdir='kb_assets')


def load_txt(path: str) -> List[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    return [{'page_or_section': 'full', 'text': text, 'images': []}]


def load_pdf(path: str) -> List[Dict[str, Any]]:
    """Extract text and embedded images from a PDF, inserting markdown image links."""
    import fitz  # PyMuPDF

    from hongwen.services.utils.ocr import ocr_image_bytes

    doc = fitz.open(path)
    segments: List[Dict[str, Any]] = []

    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)

        # Blocks keep some ordering between text and images.
        blocks = page.get_text('dict')['blocks']
        combined_text = ''
        page_images: List[str] = []

        for b in blocks:
            if b['type'] == 0:  # text block
                for line in b['lines']:
                    for span in line['spans']:
                        combined_text += span['text']
                combined_text += '\n'
            elif b['type'] == 1:  # image block
                try:
                    img_bytes = b['image']
                    ext = b['ext']
                    img_path = save_kb_image(img_bytes, ext)
                    page_images.append(img_path)
                    combined_text += f'\n![screenshot]({img_path})\n'

                    # OCR the image as hidden text for searchability (optional).
                    ocr_text = ocr_image_bytes(img_bytes)
                    if ocr_text:
                        combined_text += f'\n[OCR: {ocr_text}]\n'
                except Exception:
                    pass

        segments.append({'page_or_section': f'page_{page_idx + 1}', 'text': combined_text, 'images': page_images})

    return segments


def load_docx(path: str) -> List[Dict[str, Any]]:
    """Extract paragraph text and images from a DOCX."""
    import docx

    from hongwen.services.utils.ocr import ocr_image_bytes

    d = docx.Document(path)
    combined_text = ''
    doc_images: List[str] = []

    # python-docx does not easily interleave images and text in order; paragraphs
    # first, then images appended (a simplification carried over from helpdesk).
    for p in d.paragraphs:
        combined_text += p.text + '\n'

    try:
        for rel in d.part.rels.values():
            if 'image' not in rel.target_ref:
                continue
            img_part = rel.target_part
            img_bytes = img_part.blob
            ext = img_part.content_type.split('/')[-1]
            img_path = save_kb_image(img_bytes, ext)
            doc_images.append(img_path)
            combined_text += f'\n![screenshot]({img_path})\n'

            ocr_text = ocr_image_bytes(img_bytes)
            if ocr_text:
                combined_text += f'\n[OCR: {ocr_text}]\n'
    except Exception:
        pass

    return [{'page_or_section': 'docx', 'text': combined_text, 'images': doc_images}]


def load_knowledge_base_document(path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (content_hash, segments); segments carry page_or_section/text/images."""
    source_type = detect_source_type(path)
    file_hash = sha256_file(path)

    if source_type == 'txt':
        segments = load_txt(path)
    elif source_type == 'pdf':
        segments = load_pdf(path)
    elif source_type == 'docx':
        segments = load_docx(path)
    else:
        raise ValueError('unreachable')

    return file_hash, segments