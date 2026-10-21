"""Single choke point for every call to the RFQ language model.

Both call sites used to build and POST their own request inside
``accounts/rfq_ai.py``, which made spend both invisible and uncappable: the
provider's ``usage`` block was parsed away, and one heuristic-positive mail
could fire two requests nobody counted. Everything now goes through
``call_llm_json``, which reads the monthly budget before it builds a request,
records one ``LlmCallRecord`` per attempt, and returns a result object instead
of raising - a mailbox sweep must not die because a vendor answered with
invalid JSON.

The payload keeps its Anthropic shape because that is what the configured
endpoint already accepts; ``extract_usage`` and ``_parse_response`` also read
the OpenAI shape, so switching provider is a settings edit rather than a code
change.
"""

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib import request
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import LlmCallRecord


LOGGER = logging.getLogger(__name__)
TOKENS_PER_MTOK = Decimal('1000000')


@dataclass
class LlmResult:
    """What one attempt returned, and what it cost.

    ``payload`` is the model's own JSON object, or None when there is nothing
    usable; ``status`` says which of the outcomes occurred, which is the only
    way a caller can tell "no key configured" from "the budget is spent".
    """

    payload: dict | None
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = LlmCallRecord.Status.OK
    latency_ms: int = 0
    error: str = ''


def _month_start():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def cost_cny(input_tokens, output_tokens):
    """Price a token pair at the configured per-million-token rates."""
    price_input = Decimal(str(settings.RFQ_LLM_PRICE_PER_MTOK_INPUT))
    price_output = Decimal(str(settings.RFQ_LLM_PRICE_PER_MTOK_OUTPUT))
    return (Decimal(input_tokens or 0) * price_input + Decimal(output_tokens or 0) * price_output) / TOKENS_PER_MTOK


def spend_cny(since, until=None):
    """Estimated cost in CNY of the calls recorded in a time window.

    Only token counts are stored; the price comes from settings at read time, so
    a tier change is an `.env` edit and never a migration. The trade-off is that
    history is re-priced at the current rate, which is acceptable because the
    figure is a control on this month's spend, not an invoice.
    """
    queryset = LlmCallRecord.objects.filter(created_at__gte=since)
    if until is not None:
        queryset = queryset.filter(created_at__lt=until)
    totals = queryset.aggregate(
        input_total=Sum('input_tokens'),
        output_total=Sum('output_tokens'),
    )
    return cost_cny(totals['input_total'], totals['output_total'])


def monthly_spend_cny():
    """Estimated cost, in CNY, of every call recorded so far this UTC month."""
    return spend_cny(_month_start())


def budget_exceeded():
    """Whether this month's estimated spend has reached the configured ceiling.

    A budget of 0 means unlimited. Without that rule, a development checkout -
    which has no key and therefore no rows to count - would still be governed by
    a ceiling it could never see being hit.
    """
    budget = Decimal(str(settings.RFQ_LLM_MONTHLY_BUDGET_CNY))
    if budget <= 0:
        return False
    return monthly_spend_cny() >= budget


def _as_token_count(value):
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def _first_count(usage, keys):
    for key in keys:
        if key in usage:
            return _as_token_count(usage[key])
    return 0


def extract_usage(parsed):
    """Return ``(input_tokens, output_tokens)`` from either response envelope.

    Anthropic spells the pair ``input_tokens``/``output_tokens`` and OpenAI
    ``prompt_tokens``/``completion_tokens``. A response with no usable ``usage``
    counts as zero rather than an error: the payload can still be perfectly
    good, and discarding a classification because a vendor changed its metadata
    would be the worse failure.
    """
    usage = parsed.get('usage') if isinstance(parsed, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return (
        _first_count(usage, ('input_tokens', 'prompt_tokens')),
        _first_count(usage, ('output_tokens', 'completion_tokens')),
    )


def _parse_response(raw, log_label):
    """Return ``(payload, envelope)`` for a response body.

    The payload is the model's JSON object, unwrapped from an Anthropic
    ``content[]`` text block or an OpenAI ``output_text`` string when it is
    nested there. The envelope is the outer document, which is where ``usage``
    lives - reading only the payload is how token spend went unmeasured.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        LOGGER.warning('RFQ LLM %s response was not JSON', log_label)
        return None, {}

    envelope = parsed if isinstance(parsed, dict) else {}

    if isinstance(parsed, dict) and isinstance(parsed.get('content'), list):
        text_blocks = []
        for block in parsed['content']:
            if isinstance(block, dict) and block.get('type') == 'text' and block.get('text'):
                text_blocks.append(block['text'])
        if text_blocks:
            candidate_text = '\n'.join(text_blocks).strip()
            try:
                parsed = json.loads(candidate_text)
            except json.JSONDecodeError:
                LOGGER.warning('RFQ LLM %s text block was not valid JSON', log_label)
                return None, envelope
    elif isinstance(parsed, dict) and 'output_text' in parsed:
        try:
            parsed = json.loads(parsed['output_text'])
        except (TypeError, json.JSONDecodeError):
            LOGGER.warning('RFQ LLM %s output_text was not valid JSON', log_label)
            return None, envelope

    return (parsed if isinstance(parsed, dict) else None), envelope


def _record(purpose, status, *, mailbox=None, message=None, provider='', model='',
            input_tokens=0, output_tokens=0, latency_ms=0, error=''):
    """Write the telemetry row for one attempt.

    Attribution falls back to the message's own mailbox because product
    disambiguation is called from deep inside item matching, where no mailbox is
    in scope; threading one through three signatures for a report column is not
    worth it, so those rows simply carry no mailbox.
    """
    return LlmCallRecord.objects.create(
        purpose=purpose,
        status=status,
        provider=provider,
        model=model,
        mailbox_id=(mailbox.pk if mailbox is not None else None) or getattr(message, 'mailbox_id', None),
        message=message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        error=error,
    )


def record_skipped_call(purpose, *, mailbox=None, message=None, reason=''):
    """Record that the pre-gate kept a mail away from the model.

    Skips are counted, not just called: the order-of-magnitude reduction the
    gate is supposed to buy cannot otherwise be measured, and an operator
    cannot tell "the gate worked" from "the mailbox was empty".
    """
    return _record(purpose, LlmCallRecord.Status.SKIPPED, mailbox=mailbox, message=message, error=reason)


def call_llm_json(system_prompt, user_prompt, *, purpose, mailbox=None, message=None):
    """POST a JSON-only prompt and return an ``LlmResult``. Never raises.

    Every outcome is recorded, including the attempts refused before the
    network was touched, so the monthly total is a count of decisions and not
    just of billable calls.
    """
    try:
        return _call_llm_json(system_prompt, user_prompt, purpose=purpose, mailbox=mailbox, message=message)
    except Exception as exc:
        LOGGER.warning('RFQ LLM %s call failed unexpectedly: %s', purpose, exc)
        return LlmResult(payload=None, status=LlmCallRecord.Status.ERROR, error=str(exc))


def _call_llm_json(system_prompt, user_prompt, *, purpose, mailbox, message):
    api_key = settings.RFQ_LLM_API_KEY
    api_url = settings.RFQ_LLM_API_URL
    provider = urlparse(api_url).hostname or ''
    # Must always be an explicit model name, never a retired alias: an unknown
    # alias is rejected by the provider and surfaces only as heuristic-only
    # classification, not as an error.
    model = settings.RFQ_LLM_MODEL or 'MiniMax-M2.7'
    log_label = f'{purpose} for message {message.pk}' if message is not None else purpose

    if budget_exceeded():
        error = f'Monthly RFQ LLM budget of CNY {settings.RFQ_LLM_MONTHLY_BUDGET_CNY} reached.'
        LOGGER.warning('RFQ LLM %s not attempted: %s', log_label, error)
        _record(purpose, LlmCallRecord.Status.BUDGET_EXCEEDED, mailbox=mailbox, message=message,
                provider=provider, model=model, error=error)
        return LlmResult(payload=None, status=LlmCallRecord.Status.BUDGET_EXCEEDED, error=error)

    if not api_key or not api_url:
        error = 'No RFQ LLM API key or endpoint configured.'
        _record(purpose, LlmCallRecord.Status.NOT_CONFIGURED, mailbox=mailbox, message=message, error=error)
        return LlmResult(payload=None, status=LlmCallRecord.Status.NOT_CONFIGURED, error=error)

    content = user_prompt if isinstance(user_prompt, str) else json.dumps(user_prompt, ensure_ascii=False)
    payload = json.dumps(
        {
            'model': model,
            'system': system_prompt,
            'max_tokens': int(settings.RFQ_LLM_MAX_TOKENS),
            'temperature': 0.2,
            'messages': [
                {
                    'role': 'user',
                    'content': content,
                }
            ],
        },
        ensure_ascii=False,
    ).encode('utf-8')
    req = request.Request(
        api_url,
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    started_at = time.monotonic()
    try:
        with request.urlopen(req, timeout=float(settings.RFQ_LLM_TIMEOUT_SECONDS)) as response:
            raw = response.read().decode('utf-8')
    except Exception as exc:
        latency_ms = int((time.monotonic() - started_at) * 1000)
        LOGGER.warning('RFQ LLM %s request failed: %s', log_label, exc)
        _record(purpose, LlmCallRecord.Status.ERROR, mailbox=mailbox, message=message, provider=provider,
                model=model, latency_ms=latency_ms, error=str(exc))
        return LlmResult(payload=None, status=LlmCallRecord.Status.ERROR, latency_ms=latency_ms, error=str(exc))

    latency_ms = int((time.monotonic() - started_at) * 1000)
    parsed, envelope = _parse_response(raw, log_label)
    input_tokens, output_tokens = extract_usage(envelope)
    status = LlmCallRecord.Status.OK if parsed is not None else LlmCallRecord.Status.ERROR
    error = '' if parsed is not None else 'Response contained no usable JSON object.'
    _record(purpose, status, mailbox=mailbox, message=message, provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=latency_ms, error=error)
    return LlmResult(payload=parsed, input_tokens=input_tokens, output_tokens=output_tokens,
                     status=status, latency_ms=latency_ms, error=error)
