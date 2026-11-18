"""KB vector retrieval on the Django ORM, dual-backend (PostgreSQL / SQLite).

Rewrite of helpdesk/backend/app/kb/vector_store.py. The helpdesk used a pgvector
``vector`` column; here ``KBChunk.embedding`` is a JSONField (jsonb) list, so:

* PostgreSQL: raw SQL casts at query time,
  ``(embedding::text)::vector <=> CAST(%s AS vector)`` (cosine distance),
  ``score = 1 - distance``. No ivfflat index - a jsonb column cannot carry one;
  that is deferred performance debt (the KB is small enough for a seq scan).
* SQLite (dev/test): a Python brute-force cosine over the customer-scoped chunks.

Customer scoping matches the helpdesk: a customer sees its own documents PLUS
global (NULL-customer) ones; no customer sees only global. Embeddings are padded
or truncated to 1536 dims on both ingestion and query so a provider swap stays
aligned with the stored schema.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from django.db import connection
from django.db.models import Q

from wenqu.models import KBChunk, KBDocument
from wenqu.services.llm.model_selector import select_primary_embed_ref
from wenqu.services.llm.provider_router import embed_text

EMBEDDING_DIMENSION = 1536


def _to_vector_literal(vec: List[float]) -> str:
    """pgvector expects '[0.1,0.2,...]'; a Python list would bind as numeric[] and break <=>."""
    return '[' + ','.join(f'{float(x):.12g}' for x in vec) + ']'


def _coerce_embedding_dimension(vec: List[float]) -> List[float]:
    if len(vec) == EMBEDDING_DIMENSION:
        return vec
    if len(vec) > EMBEDDING_DIMENSION:
        return vec[:EMBEDDING_DIMENSION]
    return vec + [0.0] * (EMBEDDING_DIMENSION - len(vec))


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity for the SQLite brute-force path (0.0 for a zero vector)."""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def embed_query(query: str) -> List[float]:
    model = select_primary_embed_ref().qualified_name
    embeddings = embed_text(model=model, texts=[query])
    return _coerce_embedding_dimension(embeddings[0])


def _customer_scope(customer_id: Optional[int]) -> Q:
    if customer_id is not None:
        return Q(document__customer_id=customer_id) | Q(document__customer_id__isnull=True)
    return Q(document__customer_id__isnull=True)


def _row_to_entry(source: str, snippet: str, score: float, page_or_section: str, metadata: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if isinstance(metadata, dict):
        meta = metadata
    elif metadata:
        try:
            meta = json.loads(metadata)
        except Exception:
            meta = {}
    return {
        'source': source,
        'snippet': snippet,
        'score': float(score),
        'page_or_section': page_or_section or '',
        'metadata': meta,
    }


def _search_postgres(q_vec: List[float], customer_id: Optional[int], top_k: int) -> List[Dict[str, Any]]:
    chunk_table = KBChunk._meta.db_table
    doc_table = KBDocument._meta.db_table
    q_literal = _to_vector_literal(q_vec)

    if customer_id is not None:
        customer_filter = f'JOIN {doc_table} d ON c.document_id = d.id WHERE (d.customer_id = %s OR d.customer_id IS NULL)'
        params: List[Any] = [q_literal, customer_id, q_literal, top_k]
    else:
        customer_filter = f'JOIN {doc_table} d ON c.document_id = d.id WHERE d.customer_id IS NULL'
        params = [q_literal, q_literal, top_k]

    sql = f"""
        SELECT
          c.content,
          c.source_path,
          c.page_or_section,
          c.metadata_json,
          (1 - ((c.embedding::text)::vector <=> CAST(%s AS vector))) AS score
        FROM {chunk_table} c
        {customer_filter}
        ORDER BY (c.embedding::text)::vector <=> CAST(%s AS vector)
        LIMIT %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    return [
        _row_to_entry(source, content, score, page_or_section, metadata)
        for content, source, page_or_section, metadata, score in rows
    ]


def _search_sqlite(q_vec: List[float], customer_id: Optional[int], top_k: int) -> List[Dict[str, Any]]:
    chunks = KBChunk.objects.filter(_customer_scope(customer_id)).select_related('document')
    ranked: List[Dict[str, Any]] = []
    for chunk in chunks.iterator():
        embedding = chunk.embedding or []
        score = cosine_similarity(q_vec, _coerce_embedding_dimension(embedding))
        ranked.append(_row_to_entry(
            chunk.source_path, chunk.content, score, chunk.page_or_section, chunk.metadata_json,
        ))
    ranked.sort(key=lambda entry: entry['score'], reverse=True)
    return ranked[:top_k]


def search_kb(*, query: str, customer_id: Optional[int] = None, top_k: int = 5) -> List[Dict[str, Any]]:
    """Return passages sorted by descending relevance, scoped to the customer."""
    if not query.strip():
        return []

    q_vec = embed_query(query)
    if connection.vendor == 'postgresql':
        return _search_postgres(q_vec, customer_id, top_k)
    return _search_sqlite(q_vec, customer_id, top_k)