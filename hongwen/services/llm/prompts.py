"""System-prompt rendering and per-model prompt packs for the Hongwen helpdesk.

Port of helpdesk/backend/app/llm/prompts.py. The persona is branded 泓问. Pack
markdown lives in ``template_files/hongwen/prompt_packs/`` (Django app directories
must not hold .md files - tests/test_repo_hygiene.py), loaded relative to
``settings.BASE_DIR``. Temperature/mode semantics are unchanged: chat modes ask
for a single JSON object, stream modes for plain text; regenerate modes add the
re-answer rules.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal

from django.conf import settings

PromptMode = Literal['chat', 'chat_regenerate', 'stream', 'stream_regenerate']

_PROMPT_LOCALE = 'zh-CN'
_REQUIRED_SECTIONS = ('SOUL', 'IDENTITY', 'USER', 'AGENTS')
_MODEL_PROMPT_PACKS = {
    'volcengine/doubao-seed-2-0-lite-260428': 'doubao-seed-2.0',
    'qwen/qwen3.6-plus': 'qwen-3.6',
}

_DEFAULT_SECTION_CONTENT = {
    'SOUL': '你是「泓问」，面向微信小程序场景的中文帮助台专家。你的语气稳重、耐心、直接，优先帮助用户完成排查与恢复，而不是给空泛建议。',
    'IDENTITY': '你的职责是「泓问」帮助台助手，不是客服派单系统，也不是售后审批系统。除非知识库明确写出流程或政策，否则不得代表公司承诺上门、派工程师、退款、补偿、回访、升级工单或任何线下执行动作。',
    'USER': '你的主要服务对象是中文用户。默认使用简体中文；只有当用户明确使用其他语言或明确要求时，才切换语言。回答要短句、分步骤、可执行，并根据 user_profile 中的薄弱点调整解释粒度。',
    'AGENTS': '优先使用传入的 knowledge_base；如果知识库不足，就明确说明已知边界并提出 1 到 3 个必要澄清问题。若知识库中包含截图或图片引用，只输出文字指导，不要输出 markdown 图片、/uploads 链接或 kb_assets 路径。',
}

_COMMON_CHAT_RULES = """输入字段：
1. conversation_history：最近对话。
2. latest_user_input：本轮用户输入。
3. user_profile：用户当前薄弱点、建议方案、备注。
4. knowledge_base：检索到的知识片段，可为空。

执行规则：
- 优先依据 knowledge_base 中与问题直接相关的内容回答。
- 如果缺少关键条件，先提出最少量的澄清问题，再继续排查。
- 不要编造后台操作结果、物流结果、工单状态、人工承诺或上门安排。
- 当知识库不足以支持结论时，明确说明“不确定/需要确认”，然后给出下一步检查建议。
- 将建议组织成用户现在就能执行的步骤；必要时说明预期结果和分支判断。"""

_COMMON_JSON_RULES = """输出要求：
- 只输出单个 JSON 对象，不要输出 Markdown，不要输出代码块。
- assistant_reply 必须是纯文本，不要带标题层级、表格、图片标记或原始文件路径。
- assistant_reply 默认使用简体中文；用户明确要求英文时再切换。
- JSON 结构必须为：
{
  "assistant_reply": "<发给用户的纯文本回复>",
  "updated_weak_points": "<JSON 字符串或项目符号文本>",
  "updated_suggested_solutions": "<JSON 字符串或项目符号文本>",
  "meta": {
  "used_knowledge_base": true,
  "knowledge_sources": ["<source>"]
  }
}
- 除这个 JSON 对象外，不得输出任何额外文本。"""

_COMMON_STREAM_RULES = """输出要求：
- 只输出纯文本，不要输出 JSON。
- 不要输出 Markdown 代码块。
- 默认使用简体中文；用户明确要求英文时再切换。
- 保持步骤清楚，单次输出可分多句，但不要加入与排障无关的寒暄。"""

_REGENERATE_RULES = """重生成规则：
- 用户要求针对同一问题重新回答。
- 新回答必须在表达、排查顺序或组织方式上明显不同于上一版。
- 事实必须仍然与对话和 knowledge_base 保持一致。
- 除非用户主动提到，否则不要说“这是重生成版本”。"""


@dataclass(frozen=True)
class PromptPack:
    pack_name: str
    locale: str
    sections: Dict[str, str]


def _prompt_pack_root() -> Path:
    return Path(settings.BASE_DIR) / 'template_files' / 'hongwen' / 'prompt_packs'


def _normalize_model_name(model: str) -> str:
    return str(model or '').strip().lower()


def _resolve_pack_name(model: str) -> str:
    normalized = _normalize_model_name(model)
    if normalized in _MODEL_PROMPT_PACKS:
        return _MODEL_PROMPT_PACKS[normalized]
    if normalized.startswith('volcengine/'):
        return 'doubao-seed-2.0'
    if normalized.startswith('qwen/') or normalized.startswith('dashscope/'):
        return 'qwen-3.6'
    return 'default-zh'


def _read_section_text(pack_dir: Path, section_name: str) -> str:
    section_path = pack_dir / f'{section_name}.{_PROMPT_LOCALE}.md'
    if not section_path.exists():
        return _DEFAULT_SECTION_CONTENT[section_name]
    return section_path.read_text(encoding='utf-8').strip()


def load_prompt_pack(model: str) -> PromptPack:
    pack_name = _resolve_pack_name(model)
    pack_dir = _prompt_pack_root() / pack_name
    sections = {
        section_name: _read_section_text(pack_dir, section_name)
        for section_name in _REQUIRED_SECTIONS
    }
    return PromptPack(pack_name=pack_name, locale=_PROMPT_LOCALE, sections=sections)


def render_helpdesk_system_prompt(*, model: str, mode: PromptMode) -> str:
    pack = load_prompt_pack(model)
    parts = [
        f'[Prompt Pack] {pack.pack_name}',
        f'[Locale] {pack.locale}',
    ]
    for section_name in _REQUIRED_SECTIONS:
        parts.append(f'[{section_name}]\n{pack.sections[section_name]}')
    parts.append(f'[WORKFLOW]\n{_COMMON_CHAT_RULES}')
    if mode in {'chat', 'chat_regenerate'}:
        parts.append(f'[OUTPUT]\n{_COMMON_JSON_RULES}')
    else:
        parts.append(f'[OUTPUT]\n{_COMMON_STREAM_RULES}')
    if mode in {'chat_regenerate', 'stream_regenerate'}:
        parts.append(f'[REGENERATE]\n{_REGENERATE_RULES}')
    return '\n\n'.join(parts)


def build_llm_messages(
    *,
    conversation_history: List[Dict[str, Any]],
    latest_user_input: Dict[str, Any],
    user_profile: Dict[str, Any],
    knowledge_base: List[Dict[str, Any]],
    model: str,
    mode: PromptMode = 'chat',
) -> List[Dict[str, Any]]:
    payload = {
        'conversation_history': conversation_history,
        'latest_user_input': latest_user_input,
        'user_profile': user_profile,
        'knowledge_base': knowledge_base,
    }
    return [
        {'role': 'system', 'content': render_helpdesk_system_prompt(model=model, mode=mode)},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ]