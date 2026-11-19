"""KB ingestion on the Django ORM.

Rewrite of helpdesk/backend/app/kb/ingest.py. Source documents are read with
plain ``open()`` from ``HONGWEN_KB_SOURCES_DIR`` (kb.paths); chunks/embeddings are
stored on ``KBDocument`` / ``KBChunk`` (embedding is a JSONField list). The
helpdesk's SQLAlchemy Session commits become Django autocommit / transaction
blocks.

Note: ``KBDocument`` has a CharField named ``path``; the repo-hygiene media guard
matches the dot-path attribute idiom, so reads/writes go through getattr/setattr
(see hongwen/models.py for the same convention).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import Count

from hongwen.models import KBChunk, KBDocument
from hongwen.services.kb.loader import detect_source_type, load_knowledge_base_document
from hongwen.services.kb.paths import resolve_stored_path, sources_root, to_stored_path
from hongwen.services.kb.vector_store import embed_query
from hongwen.services.utils.chunking import chunk_text, extract_image_paths, extract_qa_pairs
from hongwen.services.utils.file_store import delete_file_if_exists, kb_document_display_name


def _doc_path(document: KBDocument) -> str:
    return getattr(document, 'path')


def _iter_files(sources_dir: str) -> List[str]:
    supported = {'.pdf', '.docx', '.doc', '.txt'}
    out: List[str] = []
    for root, _, files in os.walk(sources_dir):
        for name in files:
            # A leading underscore marks a scratch/fixture document that must never
            # enter the real index (kb_sources/ is a live bind mount on the host).
            if name.startswith('_'):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in supported:
                out.append(os.path.join(root, name))
    return out


def rebuild_kb() -> None:
    KBChunk.objects.all().delete()
    KBDocument.objects.all().delete()


def list_customer_documents(*, customer_id: Optional[int]) -> List[Dict[str, Any]]:
    """Stable, customer-scoped document listing with chunk counts (admin table)."""
    rows = (
        KBDocument.objects.filter(customer_id=customer_id)
        .annotate(chunk_count=Count('chunks'))
        .order_by('-created_at', '-id')
    )
    documents: List[Dict[str, Any]] = []
    for document in rows:
        documents.append({
            'id': document.id,
            'display_name': kb_document_display_name(_doc_path(document)),
            'source_type': document.source_type,
            'created_at': document.created_at.isoformat(),
            'chunk_count': int(document.chunk_count or 0),
        })
    return documents


def get_customer_document(*, customer_id: Optional[int], document_id: int) -> Optional[KBDocument]:
    """Resolve one document within a customer boundary (replace/delete/open guard)."""
    return KBDocument.objects.filter(id=document_id, customer_id=customer_id).first()


def find_customer_document_by_display_name(*, customer_id: Optional[int], display_name: str) -> Optional[KBDocument]:
    """Same-name overwrite matches the human-visible filename, not the storage prefix."""
    normalized_name = display_name.strip()
    if not normalized_name:
        return None
    for candidate in KBDocument.objects.filter(customer_id=customer_id):
        if kb_document_display_name(_doc_path(candidate)) == normalized_name:
            return candidate
    return None


def get_document_preview(
    *, customer_id: Optional[int], document_id: int, chunk_limit: int = 5, snippet_length: int = 450,
) -> Optional[Dict[str, Any]]:
    """Preview extracted chunks (not raw PDF/DOCX) so admins see what retrieval searches."""
    document = get_customer_document(customer_id=customer_id, document_id=document_id)
    if not document:
        return None

    chunks = KBChunk.objects.filter(document_id=document.id).order_by('chunk_index')[:chunk_limit]
    preview_chunks = [
        {
            'chunk_index': chunk.chunk_index,
            'chunk_type': chunk.chunk_type,
            'page_or_section': chunk.page_or_section,
            'snippet': chunk.content[:snippet_length].strip(),
        }
        for chunk in chunks
    ]
    return {
        'id': document.id,
        'display_name': kb_document_display_name(_doc_path(document)),
        'source_type': document.source_type,
        'created_at': document.created_at.isoformat(),
        'chunk_count': KBChunk.objects.filter(document_id=document.id).count(),
        'chunks': preview_chunks,
    }


def _reindex_document_chunks(*, document: KBDocument, path: str, segments: List[Dict[str, Any]]) -> int:
    """The expensive, correctness-sensitive part: QA extraction, chunking, embedding."""
    KBChunk.objects.filter(document_id=document.id).delete()

    chunk_index = 0
    for seg in segments:
        seg_text = seg.get('text', '')
        if not seg_text.strip():
            continue

        raw_seg_images = seg.get('images') or []
        if not isinstance(raw_seg_images, list):
            raw_seg_images = []
        seg_images = list(dict.fromkeys(
            [str(p) for p in raw_seg_images if p] + extract_image_paths(seg_text)
        ))

        qa_pairs = extract_qa_pairs(seg_text)
        if qa_pairs:
            for qa in qa_pairs:
                emb = embed_query(qa['question'])
                qa_metadata = dict(qa.get('metadata') or {})
                qa_image_paths = list(dict.fromkeys(
                    [str(p) for p in qa_metadata.get('images', []) if p]
                    + [str(p) for p in qa_metadata.get('image_paths', []) if p]
                    + seg_images
                ))
                qa_metadata['images'] = qa_image_paths
                qa_metadata['image_paths'] = qa_image_paths
                KBChunk.objects.create(
                    document=document,
                    chunk_index=chunk_index,
                    content=qa['text'],
                    chunk_type='qa_pair',
                    metadata_json=qa_metadata,
                    embedding=emb,
                    source_path=path,
                    page_or_section=seg.get('page_or_section', ''),
                )
                chunk_index += 1
        else:
            for part in chunk_text(seg_text, chunk_size=900, overlap=150):
                if not part.strip():
                    continue
                emb = embed_query(part)
                part_image_paths = list(dict.fromkeys(seg_images + extract_image_paths(part)))
                KBChunk.objects.create(
                    document=document,
                    chunk_index=chunk_index,
                    content=part,
                    chunk_type='text',
                    metadata_json={'images': part_image_paths, 'image_paths': part_image_paths},
                    embedding=emb,
                    source_path=path,
                    page_or_section=seg.get('page_or_section', ''),
                )
                chunk_index += 1

    return chunk_index


def delete_document(*, document: KBDocument) -> None:
    """Remove retrieval rows AND the source artifact so list/search/dir stay in sync."""
    source_path = _doc_path(document)
    with transaction.atomic():
        KBChunk.objects.filter(document_id=document.id).delete()
        document.delete()
    delete_file_if_exists(resolve_stored_path(source_path))


def replace_document(*, document: KBDocument, path: str, customer_id: Optional[int]) -> KBDocument:
    """Row-level replacement: refresh source file, chunks and customer scoping."""
    stored_path = to_stored_path(path)
    content_hash, segments = load_knowledge_base_document(resolve_stored_path(path))
    source_type = detect_source_type(stored_path)
    previous_path = _doc_path(document)
    needs_reindex = (
        previous_path != stored_path
        or document.content_hash != content_hash
        or document.source_type != source_type
        or document.customer_id != customer_id
    )

    with transaction.atomic():
        setattr(document, 'path', stored_path)
        document.source_type = source_type
        document.content_hash = content_hash
        document.customer_id = customer_id
        document.save()
        if needs_reindex:
            _reindex_document_chunks(document=document, path=stored_path, segments=segments)

    if previous_path != stored_path:
        delete_file_if_exists(resolve_stored_path(previous_path))
    return document


def ingest_one(path: str, customer_id: Optional[int] = None, existing_document: Optional[KBDocument] = None) -> KBDocument:
    """Standard upload / same-name overwrite entrypoint (create or replace)."""
    stored_path = to_stored_path(path)
    content_hash, segments = load_knowledge_base_document(resolve_stored_path(path))
    src_type = detect_source_type(stored_path)

    doc = existing_document or KBDocument.objects.filter(path=stored_path).first()
    if not doc:
        with transaction.atomic():
            doc = KBDocument.objects.create(
                path=stored_path, source_type=src_type, content_hash=content_hash, customer_id=customer_id,
            )
            _reindex_document_chunks(document=doc, path=stored_path, segments=segments)
        return doc

    previous_path = _doc_path(doc)
    needs_reindex = (
        previous_path != stored_path
        or doc.content_hash != content_hash
        or doc.source_type != src_type
        or doc.customer_id != customer_id
    )

    with transaction.atomic():
        setattr(doc, 'path', stored_path)
        doc.source_type = src_type
        doc.content_hash = content_hash
        doc.customer_id = customer_id
        doc.save()
        if needs_reindex:
            _reindex_document_chunks(document=doc, path=stored_path, segments=segments)

    if previous_path != stored_path:
        delete_file_if_exists(resolve_stored_path(previous_path))
    return doc


def ingest_directory(sources_dir: Optional[str] = None, *, customer_id: Optional[int] = None, rebuild: bool = False) -> int:
    """Walk a sources directory and ingest every supported file (CLI/management hook).

    Returns the number of documents ingested. PR4/PR7 can wrap this in a
    management command; the helpdesk's argparse ``main`` is not ported.
    """
    directory = os.path.abspath(sources_dir) if sources_dir else sources_root()
    if not os.path.isdir(directory):
        raise RuntimeError(f'KB sources dir not found: {directory}')
    if rebuild:
        rebuild_kb()
    count = 0
    for path in _iter_files(directory):
        ingest_one(path, customer_id=customer_id)
        count += 1
    return count