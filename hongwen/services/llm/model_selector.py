"""Provider/model selection for the Hongwen LLM services.

Port of helpdesk/backend/app/llm/model_selector.py. The helpdesk froze these
refs into module constants at import time; here they are computed lazily from
Django settings on every call so ``override_settings`` works in tests and an
operator can change models without a restart. The public ``select_*`` API and
the vendor defaults are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class ProviderModelRef:
    provider: str
    model_id: str

    @property
    def qualified_name(self) -> str:
        return f'{self.provider}/{self.model_id}'


_PROVIDER_ALIASES = {
    '': 'dashscope',
    'aliyun': 'dashscope',
    'aliyuncs': 'dashscope',
    'dashscope': 'dashscope',
    'qwen': 'dashscope',
    'volc': 'volcengine',
    'volcengine': 'volcengine',
    'ark': 'volcengine',
    'doubao': 'volcengine',
}

_DEFAULT_TEXT_MODEL = 'qwen/qwen3.6-plus'
_DEFAULT_VISION_MODEL = 'qwen/qwen3-vl-flash'
_DEFAULT_ASR_MODEL = 'qwen/qwen3-asr-flash'
_DEFAULT_EMBED_MODEL = 'qwen/text-embedding-v4'
_DEFAULT_TTS_MODEL = 'qwen/cosyvoice-v3-flash'
_DEFAULT_BACKUP_TTS_MODEL = 'volcengine/seed-tts-2.0'
_DEFAULT_OMNI_MODEL = 'qwen/xiaomi/mimo-v2-omni'
_DEFAULT_TTS_DEFAULT_VOICE = 'longanyang'
_DEFAULT_VOLCENGINE_TTS_DEFAULT_VOICE = 'zh_male_kailangxuezhang_uranus_bigtts'


def _setting(name: str) -> str:
    return str(getattr(settings, name, '') or '').strip()


def _normalize_provider(provider: str | None, *, default: str = 'dashscope') -> str:
    normalized = str(provider or '').strip().lower()
    if not normalized:
        return default
    return _PROVIDER_ALIASES.get(normalized, normalized)


def parse_model_ref(value: str, *, default_provider: str = 'dashscope') -> ProviderModelRef:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError('Model reference cannot be empty')

    provider, separator, model_id = normalized.partition('/')
    if not separator or not model_id.strip():
        return ProviderModelRef(provider=_normalize_provider(default_provider), model_id=normalized)

    normalized_provider = _normalize_provider(provider, default=default_provider)
    return ProviderModelRef(provider=normalized_provider, model_id=model_id.strip())


def _ref_from_setting(name: str, default: str) -> ProviderModelRef:
    return parse_model_ref(_setting(name) or default)


def _first_ref(*refs: ProviderModelRef | None, default: str) -> ProviderModelRef:
    for ref in refs:
        if ref and ref.model_id:
            return ref
    return parse_model_ref(default)


def _primary_omni_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_OMNI_MODEL', _DEFAULT_OMNI_MODEL)


def _backup_omni_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_OMNI_MODEL', _primary_omni_ref().qualified_name)


def _primary_text_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_TEXT_MODEL', _setting('PRIMARY_OMNI_MODEL') or _DEFAULT_TEXT_MODEL)


def _backup_text_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_TEXT_MODEL', _setting('BACKUP_OMNI_MODEL') or _primary_text_ref().qualified_name)


def _primary_vision_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_VISION_MODEL', _setting('PRIMARY_OMNI_MODEL') or _DEFAULT_VISION_MODEL)


def _backup_vision_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_VISION_MODEL', _setting('BACKUP_OMNI_MODEL') or _primary_vision_ref().qualified_name)


def _primary_asr_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_ASR_MODEL', _DEFAULT_ASR_MODEL)


def _backup_asr_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_ASR_MODEL', _primary_asr_ref().qualified_name)


def _primary_embed_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_EMBED_MODEL', _DEFAULT_EMBED_MODEL)


def _backup_embed_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_EMBED_MODEL', _primary_embed_ref().qualified_name)


def _primary_tts_ref() -> ProviderModelRef:
    return _ref_from_setting('PRIMARY_TTS_MODEL', _DEFAULT_TTS_MODEL)


def _backup_tts_ref() -> ProviderModelRef:
    return _ref_from_setting('BACKUP_TTS_MODEL', _DEFAULT_BACKUP_TTS_MODEL)


def select_primary_text_ref() -> ProviderModelRef:
    return _primary_text_ref()


def select_backup_text_ref() -> ProviderModelRef:
    return _backup_text_ref()


def select_primary_vision_ref() -> ProviderModelRef:
    return _primary_vision_ref()


def select_backup_vision_ref() -> ProviderModelRef:
    return _backup_vision_ref()


def select_primary_asr_ref() -> ProviderModelRef:
    return _primary_asr_ref()


def select_backup_asr_ref() -> ProviderModelRef:
    return _backup_asr_ref()


def select_primary_embed_ref() -> ProviderModelRef:
    return _primary_embed_ref()


def select_backup_embed_ref() -> ProviderModelRef:
    return _backup_embed_ref()


def select_primary_tts_ref() -> ProviderModelRef:
    return _primary_tts_ref()


def select_backup_tts_ref() -> ProviderModelRef:
    return _backup_tts_ref()


def select_primary_omni_ref(*, multimodal: bool = False) -> ProviderModelRef:
    primary_omni = _primary_omni_ref()
    if primary_omni.model_id and primary_omni.qualified_name != parse_model_ref(_DEFAULT_OMNI_MODEL).qualified_name:
        return primary_omni
    if multimodal:
        return _first_ref(_primary_vision_ref(), _primary_text_ref(), default=_DEFAULT_OMNI_MODEL)
    return _first_ref(_primary_text_ref(), _primary_vision_ref(), default=_DEFAULT_OMNI_MODEL)


def select_fallback_omni_ref(*, multimodal: bool = False, primary_ref: ProviderModelRef | None = None) -> ProviderModelRef:
    fallback_omni = _backup_omni_ref()
    if fallback_omni.model_id and fallback_omni.qualified_name != _primary_omni_ref().qualified_name:
        return fallback_omni
    if multimodal:
        return primary_ref or _first_ref(_backup_vision_ref(), _backup_text_ref(), default=_DEFAULT_OMNI_MODEL)
    return _first_ref(_backup_text_ref(), _backup_vision_ref(), primary_ref, default=_DEFAULT_OMNI_MODEL)


def select_primary_text_model() -> str:
    return _primary_text_ref().model_id


def select_backup_text_model() -> str:
    return _backup_text_ref().model_id


def select_primary_vision_model() -> str:
    return _primary_vision_ref().model_id


def select_backup_vision_model() -> str:
    return _backup_vision_ref().model_id


def select_primary_omni_model(*, multimodal: bool = False) -> str:
    return select_primary_omni_ref(multimodal=multimodal).model_id


def select_fallback_omni_model(*, multimodal: bool = False, primary_model: str | None = None) -> str:
    primary_ref = parse_model_ref(primary_model) if primary_model else None
    return select_fallback_omni_ref(multimodal=multimodal, primary_ref=primary_ref).model_id


def select_asr_model() -> str:
    return _primary_asr_ref().model_id


def select_backup_asr_model() -> str:
    return _backup_asr_ref().model_id


def select_embed_model() -> str:
    return _primary_embed_ref().model_id


def select_backup_embed_model() -> str:
    return _backup_embed_ref().model_id


def select_tts_model() -> str:
    return _primary_tts_ref().model_id


def select_backup_tts_model() -> str:
    return _backup_tts_ref().model_id


def select_tts_default_voice(model: str | ProviderModelRef | None = None) -> str:
    if isinstance(model, ProviderModelRef):
        model_ref = model
    else:
        model_ref = parse_model_ref(str(model or _primary_tts_ref().qualified_name))

    voice = _setting('QWEN_TTS_DEFAULT_VOICE') or _DEFAULT_TTS_DEFAULT_VOICE
    if model_ref.provider == 'volcengine':
        return _setting('VOLCENGINE_TTS_DEFAULT_VOICE') or _DEFAULT_VOLCENGINE_TTS_DEFAULT_VOICE

    model_id = model_ref.model_id.strip().lower()
    if model_id.startswith('cosyvoice') and voice.lower() in {'', 'serena', 'bella'}:
        return 'longanyang'
    return voice