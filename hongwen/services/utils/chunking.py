"""Text chunking and Q&A / image extraction for KB ingestion.

Direct port of helpdesk/backend/app/utils/chunking.py (pure Python, no I/O).
"""
from __future__ import annotations

import re
from typing import List


def extract_image_paths(text: str) -> List[str]:
    if not text:
        return []

    image_paths: List[str] = []
    seen: set[str] = set()
    for raw_path in re.findall(r'!\[.*?\]\((.*?)\)', text):
        normalized = str(raw_path).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        image_paths.append(normalized)
    return image_paths


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    """Simple character-based chunking with overlap.

    Good enough for a first version; can be improved with token-based chunking.
    """
    normalized = text.replace('\r\n', '\n').strip()
    if not normalized:
        return []

    chunks: List[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return chunks


def extract_qa_pairs(text: str) -> List[dict]:
    """Identify Q&A patterns and return a list of {question, answer, metadata} dicts.

    Supported patterns: ``Q: ... A: ...`` and ``Question: ... Answer: ...``.
    """
    qa_pattern = re.compile(
        r'(?i)(?:^|\n)\s*(Q(?:uestion)?\s*\d*)\s*[:：]\s*(.*?)\s*(?:^|\n)\s*(A(?:nswer)?\s*\d*)\s*[:：]\s*(.*?)(?=\n\s*(?:Q(?:uestion)?\s*\d*)\s*[:：]|$)',
        re.DOTALL,
    )

    results = []
    for m in qa_pattern.finditer(text):
        q_label = m.group(1).strip()
        q_content = m.group(2).strip()
        a_label = m.group(3).strip()
        a_content = m.group(4).strip()

        if q_content and a_content:
            images = extract_image_paths(q_content + '\n' + a_content)
            results.append({
                'type': 'qa_pair',
                'question': q_content,
                'answer': a_content,
                'text': f'{q_label}: {q_content}\n{a_label}: {a_content}',
                'metadata': {'images': images, 'is_qa': True},
            })

    return results