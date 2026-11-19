"""Deterministic, credential-free provider stubs shared by the hongwen service tests.

Ported from helpdesk/backend/tests/conftest.py. Provider functions are patched at
the *import site* (e.g. ``hongwen.services.llm.orchestrator.search_kb``), never at
the source module, because the services do ``from ... import X``.

This module is a helper, not a test module: it defines no TestCase / test_*
functions, so neither Django's loader nor pytest collects anything from it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# embeddings
# ---------------------------------------------------------------------------


def stub_vector(value: str) -> List[float]:
    """Hash whitespace tokens into a unit 1536-dim vector.

    A constant vector would make every cosine score identical and hide ranking
    bugs; this way identical text scores 1.0 against itself and shared keywords
    score above unrelated text, so retrieval tests can assert on ordering.
    """
    tokens = re.findall(r'\w+', (value or '').lower()) or ['<empty>']
    vec = [0.0] * 1536
    for token in tokens:
        digest = int.from_bytes(hashlib.sha256(token.encode('utf-8')).digest()[:8], 'big')
        vec[digest % 1536] += 1.0
    norm = math.sqrt(sum(part * part for part in vec)) or 1.0
    return [part / norm for part in vec]


_TRIAGE_DIM = 1536
_NETWORK_KEYWORDS = ['网', '网络', '上网', '联网', '断网', 'wi-fi', 'wifi', '无线', 'internet']
_PRINTER_KEYWORDS = ['打印', 'printer', '收银', 'aio']
_TOPIC_DIMS = {'network': 1, 'printer': 2}


def triage_vector(value: str) -> List[float]:
    """Topic-dimension embedding for Chinese symptoms (no whitespace to tokenize).

    The playbook pre-filter is a cosine threshold (MATCH_THRESHOLD = 0.35); a
    whole Chinese sentence would otherwise hash to one opaque token and score
    near zero. Each topic gets its own dimension plus a char-bigram floor.
    """
    vec = [0.0] * _TRIAGE_DIM
    lowered = str(value or '').lower()
    if any(keyword in lowered for keyword in _NETWORK_KEYWORDS):
        vec[_TOPIC_DIMS['network']] += 1.0
    if any(keyword in lowered for keyword in _PRINTER_KEYWORDS):
        vec[_TOPIC_DIMS['printer']] += 1.0
    chars = [character for character in lowered if not character.isspace()]
    for index in range(len(chars) - 1):
        bigram = chars[index] + chars[index + 1]
        slot = int(hashlib.md5(bigram.encode('utf-8')).hexdigest(), 16) % (_TRIAGE_DIM - 10) + 5
        vec[slot] += 0.05
    norm = math.sqrt(sum(part * part for part in vec)) or 1.0
    return [part / norm for part in vec]


# ---------------------------------------------------------------------------
# chat / TTS
# ---------------------------------------------------------------------------
DEFAULT_REPLY = '请先重启路由器，如果指示灯仍然不亮，再检查电源适配器。'
TTS_AUDIO_BYTES = b'FAKE-MP3-BYTES'


def openai_reply(content: str) -> Dict[str, Any]:
    return {
        'choices': [{'message': {'role': 'assistant', 'content': content}}],
        'finish_reason': 'stop',
    }


def fake_chat_completion(*, model: str, messages: List[Dict[str, Any]], **_: Any) -> Dict[str, Any]:
    return {**openai_reply(DEFAULT_REPLY), '_model_used': model}


def fake_chat_completion_with_fallback(
    *, primary_model: str, fallback_model: str = '', messages: List[Dict[str, Any]],
    fallback_messages: List[Dict[str, Any]] | None = None, **_: Any,
) -> Dict[str, Any]:
    return fake_chat_completion(model=primary_model, messages=messages)


def fake_stream_chat(*, primary_model: str, messages: List[Dict[str, Any]], **_: Any):
    for piece in re.findall(r'.{1,12}', DEFAULT_REPLY):
        yield {'choices': [{'delta': {'content': piece}}], '_model_used': primary_model}
    yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}], '_model_used': primary_model}


def fake_transcribe(*, model: str, file_path: str, **_: Any) -> str:
    return '测试录音转写结果'


def triage_chat_completion(*, model: str, messages: List[Dict[str, Any]], **_: Any) -> Dict[str, Any]:
    """Answer the engine's two classification prompts with keyword rules, not a model."""
    prompt = str(messages[-1].get('content') or '') if messages else ''
    if '可选排障场景' in prompt:
        query = prompt.split('用户这句话：')[-1].lower()
        slug = 'no-internet' if any(keyword in query for keyword in _NETWORK_KEYWORDS) else ''
        device = ''
        if any(token in query for token in ('ipad', 'iphone', 'ios', '苹果')):
            device = 'ios'
        elif any(token in query for token in ('windows', '电脑', 'pc')):
            device = 'windows'
        elif any(keyword in query for keyword in _PRINTER_KEYWORDS):
            device = 'printer'
        payload = json.dumps({'playbook_slug': slug, 'device': device})
    elif '可选答案' in prompt:
        payload = json.dumps({'answer': 'default'})
    else:
        payload = '{}'
    return {**openai_reply(payload), '_model_used': model}