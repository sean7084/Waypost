import logging
import re
from datetime import timedelta
from decimal import Decimal
from email.utils import parseaddr

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from assets.models import AssetCategory, AssetModel
from companies.models import Company, CompanyUser
from invoices.models import EmailDispatch
from products.models import ProductPrice
from quotations.models import Quotation, QuotationItem

from .llm_client import call_llm_json, record_skipped_call
from .models import LlmCallRecord


LOGGER = logging.getLogger(__name__)

RFQ_KEYWORDS = [
    'rfq',
    'request for quotation',
    'quotation',
    'quote',
    'pricing',
    'price list',
    'proposal',
    '请帮忙报价',
    '麻烦提供以下报价',
    '采购',
    'purchasing request',
    'procurement item',
]

GENERIC_CATEGORY_ALIASES = {
    'laptop': ['laptop', 'notebook', '笔记本'],
    'monitor': ['monitor', 'display', 'screen', '显示器'],
    'printer': ['printer', '打印机'],
    'aio': ['aio', 'all in one', 'all-in-one', '一体机'],
    'tablet': ['tablet', 'ipad', '平板', '平板电脑'],
}

# Machine-generated mail is never an RFQ, and its tell-tales are stable enough
# to match as text: a list-unsubscribe footer, an automatic reply, a bounce.
_NON_RFQ_SIGNATURES = re.compile(
    'unsubscribe|退订|list-unsubscribe|auto-submitted|automatic reply|自动回复|'
    'out of office|不在办公室|delivery status notification|undeliverable|'
    'mailer-daemon|postmaster@|no-?reply|donotreply',
    re.IGNORECASE,
)


def _normalized_text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return ' '.join(value.split()).strip()
    if isinstance(value, (int, float, Decimal)):
        return str(value).strip()
    if isinstance(value, dict):
        parts = []
        for key in ['name', 'item', 'description', 'model', 'model_number', 'brand', 'quantity', 'qty']:
            part = _normalized_text(value.get(key))
            if part:
                parts.append(part)
        if parts:
            return ' '.join(parts).strip()
        return ''
    if isinstance(value, (list, tuple, set)):
        parts = [_normalized_text(item) for item in value]
        return ' '.join(part for part in parts if part).strip()
    return ' '.join(str(value).split()).strip()


def _sender_email(message):
    return (parseaddr(message.sender or '')[1] or '').strip().lower()


def _sender_name(message):
    return _normalized_text(parseaddr(message.sender or '')[0])


def _clean_sender_name(value):
    text = _normalized_text(value)
    text = re.sub(r'^(?:发件人|寄件人|from|sender)\s*[:：]\s*', '', text, flags=re.IGNORECASE)
    return text.strip('"\' <>')


def _recognized_sender(message):
    recognized_email = _extract_original_sender_email(message)
    recognized_name = _clean_sender_name(_sender_name(message))
    text = _message_text(message)
    if recognized_email:
        pattern = re.compile(rf'([^\n<"]+?)\s*<\s*{re.escape(recognized_email)}\s*>', flags=re.IGNORECASE)
        match = pattern.search(text)
        if match:
            recognized_name = _clean_sender_name(match.group(1))
    return {
        'email': recognized_email,
        'name': recognized_name or recognized_email,
    }


def _message_text(message):
    return '\n'.join(filter(None, [message.subject, message.body_text, message.body_preview]))


def _extract_original_sender_email(message):
    body = _message_text(message)
    matches = re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', body)
    current_sender = _sender_email(message)
    for match in matches:
        lowered = match.lower()
        if lowered != current_sender:
            return lowered
    return current_sender


def _extract_labeled_value(text, labels):
    for label in labels:
        pattern = rf'{label}\s*[:：]\s*(.+)'
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _normalized_text(match.group(1).splitlines()[0])
    return ''


def _extract_phone(text):
    match = re.search(r'(?:电话|Phone|Tel)\s*[:：]?\s*([+\d\-\s]{6,20})', text, flags=re.IGNORECASE)
    return _normalized_text(match.group(1)) if match else ''


def _parse_structured_fields(message):
    text = _message_text(message)
    sender = _recognized_sender(message)
    brand = _extract_labeled_value(text, ['Brand', '品牌'])
    user = _extract_labeled_value(text, ['User', '使用人'])
    procurement = _extract_labeled_value(text, ['Procurement item', 'Procurement', '商品'])
    address = _extract_labeled_value(text, ['Address of the delivery', '联系方式', 'Delivery Address'])
    contact_phone = _extract_phone(text)
    return {
        'brand': brand,
        'user': user,
        'procurement': procurement,
        'address': address,
        'contact_phone': contact_phone,
        'contact_email': sender['email'],
        'sender_name': sender['name'],
    }


def _extract_quantity(value):
    text = _normalized_text(value)
    if not text:
        return 1
    match = re.search(
        r'(?:'
        r'(\d+)\s*(?:台|个|部|套|pcs|sets|units?)'
        r'|(?:qty|quantity)\s*[:：]?\s*(\d+)'
        r'|[x×*]\s*(\d+)'
        r')',
        text,
        flags=re.IGNORECASE,
    )
    if match:
        quantity = next((group for group in match.groups() if group), None)
        if quantity:
            return max(1, int(quantity))
    return 1


def _build_requested_item_candidates(extracted):
    candidates = []
    seen = set()
    requested_items = extracted.get('requested_items') or []
    procurement = extracted.get('procurement') or ''
    if isinstance(requested_items, dict):
        requested_items = [requested_items]
    elif not isinstance(requested_items, (list, tuple, set)):
        requested_items = [requested_items]
    if procurement:
        requested_items = [procurement] + list(requested_items)
    for raw_value in requested_items:
        text = _normalized_text(raw_value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            'raw': text,
            'quantity': _extract_quantity(text),
        })
    return candidates


def _find_category_for_request(request_text):
    normalized = _normalized_text(request_text).lower()
    if not normalized:
        return None
    categories = AssetCategory.objects.filter(is_active=True).select_related('default_asset_model__brand')
    for category in categories:
        category_name = (category.name or '').lower()
        category_code = (category.code or '').lower()
        if category_name and category_name in normalized:
            return category
        if category_code and category_code in normalized:
            return category
    for alias_key, aliases in GENERIC_CATEGORY_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            for category in categories:
                category_name = (category.name or '').lower()
                category_code = (category.code or '').lower()
                if alias_key in category_name or alias_key in category_code:
                    return category
    return None


def _extract_model_tokens(request_text):
    normalized = _normalized_text(request_text).upper()
    if not normalized:
        return []

    tokens = []
    for raw_token in re.findall(r'[A-Z0-9][A-Z0-9()/.\-]{2,}', normalized):
        token = raw_token.strip('()/.-')
        if len(token) < 3:
            continue
        if not re.search(r'[A-Z]', token):
            continue
        if not re.search(r'\d', token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _catalog_searchable_values(brand_name, model_name, model_number, description):
    combined_name = ' '.join(part for part in [brand_name, model_name] if part).strip()
    return {
        'all': [combined_name, model_name, model_number, description],
        'reverse': [combined_name, model_name, description],
    }


def _product_price_searchable_values(product_price):
    return _catalog_searchable_values(
        _normalized_text(product_price.display_brand_name).lower(),
        _normalized_text(product_price.display_name).lower(),
        _normalized_text(product_price.display_model_number).lower(),
        _normalized_text(product_price.display_description).lower(),
    )


def _asset_model_searchable_values(asset_model):
    return _catalog_searchable_values(
        _normalized_text(asset_model.brand.name).lower(),
        _normalized_text(asset_model.name).lower(),
        _normalized_text(asset_model.model_number).lower(),
        _normalized_text(asset_model.description).lower(),
    )


def _collect_catalog_text_matches(catalog_items, normalized_lower, searchable_values_getter):
    if not normalized_lower:
        return []

    exact_matches = []
    contains_matches = []
    reverse_contains_matches = []
    for catalog_item in catalog_items:
        searchable_values = searchable_values_getter(catalog_item)
        all_values = [value for value in searchable_values['all'] if value]
        reverse_values = [value for value in searchable_values['reverse'] if value]

        if any(value == normalized_lower for value in all_values):
            exact_matches.append(catalog_item)
            continue

        if any(value in normalized_lower for value in all_values):
            contains_matches.append(catalog_item)
            continue

        if len(normalized_lower) >= 4 and any(normalized_lower in value for value in reverse_values):
            reverse_contains_matches.append(catalog_item)

    return exact_matches or contains_matches or reverse_contains_matches


def _serialize_product_price_candidate(product_price):
    return {
        'product_price_id': product_price.pk,
        'brand': _normalized_text(product_price.display_brand_name),
        'model': _normalized_text(product_price.display_name),
        'model_number': _normalized_text(product_price.display_model_number),
        'description': _normalized_text(product_price.display_description),
        'unit': _normalized_text(product_price.display_unit),
        'category': _normalized_text(product_price.model.category.name) if product_price.model_id and product_price.model.category_id else '',
        'price_without_tax': str(product_price.price_without_tax),
    }


def _build_ambiguous_match_warning(requested, candidates):
    candidate_labels = []
    for candidate in candidates[:5]:
        if hasattr(candidate, 'model'):
            candidate_labels.append(str(candidate.model))
        else:
            candidate_labels.append(str(candidate))
    return {
        'type': 'ambiguous_match_unresolved',
        'requested': requested,
        'candidates': candidate_labels,
    }


def _select_product_price_candidate_with_llm(request_text, extracted, candidates):
    if len(candidates) < 2:
        return candidates[0] if candidates else None

    candidate_map = {candidate.pk: candidate for candidate in candidates if candidate.pk is not None}
    if len(candidate_map) < 2:
        return next(iter(candidate_map.values()), None)

    system_prompt = (
        '你是一个企业采购商品匹配助手。'
        '你会收到一个客户请求文本，以及一个已经缩小范围的候选商品列表。'
        '只能在候选列表里选择最匹配的一项；如果都不够确定，就返回 null。'
        '只返回 JSON，不要返回 Markdown，不要返回解释。'
    )
    user_prompt = {
        'instruction': '请从候选商品列表中选择最匹配的一项；如果无法确定，product_price_id 返回 null。',
        'request_text': request_text,
        'context': {
            'brand': _normalized_text(extracted.get('brand')),
            'user': _normalized_text(extracted.get('user')),
            'procurement': _normalized_text(extracted.get('procurement')),
        },
        'required_fields': {
            'product_price_id': '候选中的整数 ID 或 null',
            'reason': '简短中文原因',
        },
        'candidates': [_serialize_product_price_candidate(candidate) for candidate in candidates[:8]],
    }
    llm_result = call_llm_json(
        system_prompt,
        user_prompt,
        purpose=LlmCallRecord.Purpose.PRODUCT_DISAMBIGUATION,
    ).payload
    if not llm_result:
        return None

    selected_id = llm_result.get('product_price_id')
    try:
        selected_id = int(selected_id)
    except (TypeError, ValueError):
        return None
    return candidate_map.get(selected_id)


def _match_product_price_for_request(request_text, extracted):
    normalized = _normalized_text(request_text)
    normalized_lower = normalized.lower()
    all_current_prices = list(ProductPrice.objects.filter(is_current=True).select_related('brand', 'model', 'model__category', 'service_item'))
    all_current_price_model_ids = {product_price.model_id for product_price in all_current_prices if product_price.model_id}
    current_prices = list(all_current_prices)
    active_models = list(AssetModel.objects.filter(is_active=True).select_related('brand', 'category'))

    for token in _extract_model_tokens(normalized):
        model_number_matches = [
            product_price
            for product_price in current_prices
            if product_price.model_id and _normalized_text(product_price.model.model_number).lower() == token.lower()
        ]
        if len(model_number_matches) == 1:
            return model_number_matches[0], None
        if len(model_number_matches) > 1:
            llm_match = _select_product_price_candidate_with_llm(normalized, extracted, model_number_matches)
            if llm_match:
                return llm_match, None
            return None, _build_ambiguous_match_warning(normalized, model_number_matches)

        matching_models = [
            asset_model
            for asset_model in active_models
            if _normalized_text(asset_model.model_number).lower() == token.lower()
        ]
        if len(matching_models) == 1 and matching_models[0].id not in all_current_price_model_ids:
            matching_model = matching_models[0]
            return None, {
                'type': 'missing_current_price',
                'requested': normalized,
                'model': str(matching_model),
                'category': matching_model.category.name if matching_model.category_id else '',
            }
        if len(matching_models) > 1:
            return None, _build_ambiguous_match_warning(normalized, matching_models)

    category = _find_category_for_request(normalized_lower)
    if category:
        category_prices = [
            product_price
            for product_price in current_prices
            if product_price.model_id and product_price.model.category_id == category.id
        ]
        if category_prices:
            current_prices = category_prices

        category_models = [
            asset_model
            for asset_model in active_models
            if asset_model.category_id == category.id
        ]
        if category_models:
            active_models = category_models

    catalog_text_matches = _collect_catalog_text_matches(current_prices, normalized_lower, _product_price_searchable_values)
    if len(catalog_text_matches) == 1:
        return catalog_text_matches[0], None
    if len(catalog_text_matches) > 1:
        llm_match = _select_product_price_candidate_with_llm(normalized, extracted, catalog_text_matches)
        if llm_match:
            return llm_match, None
        return None, _build_ambiguous_match_warning(normalized, catalog_text_matches)

    matching_models = _collect_catalog_text_matches(active_models, normalized_lower, _asset_model_searchable_values)
    models_without_current_price = [
        asset_model
        for asset_model in matching_models
        if asset_model.id not in all_current_price_model_ids
    ]
    if len(models_without_current_price) == 1:
        matching_model = models_without_current_price[0]
        return None, {
            'type': 'missing_current_price',
            'requested': normalized,
            'model': str(matching_model),
            'category': matching_model.category.name if matching_model.category_id else '',
        }
    if len(models_without_current_price) > 1:
        return None, _build_ambiguous_match_warning(normalized, models_without_current_price)

    if category and category.default_asset_model_id:
        default_price = next(
            (
                product_price
                for product_price in all_current_prices
                if product_price.model_id == category.default_asset_model_id
            ),
            None,
        )
        if default_price:
            warning = {
                'type': 'default_model_used',
                'requested': normalized,
                'category': category.name,
                'model': str(category.default_asset_model),
            }
            return default_price, warning
        return None, {
            'type': 'missing_price_for_default_model',
            'requested': normalized,
            'category': category.name,
            'model': str(category.default_asset_model),
        }

    if category:
        return None, {
            'type': 'missing_default_model',
            'requested': normalized,
            'category': category.name,
        }

    return None, {
        'type': 'unmatched_request',
        'requested': normalized,
    }


def _sync_quotation_items_from_rfq(quotation, extracted):
    item_candidates = _build_requested_item_candidates(extracted)
    warnings = []
    matched_items = []
    cleared_existing_items = False

    if not item_candidates:
        extracted['item_matching'] = {
            'matched_items': [],
            'warnings': [],
        }
        return []

    if quotation.requires_confirmation and quotation.status == Quotation.QuotationStatus.DRAFT:
        quotation.items.all().delete()
        cleared_existing_items = True

    for candidate in item_candidates:
        product_price, warning = _match_product_price_for_request(candidate['raw'], extracted)
        if warning:
            warnings.append(warning)
        if not product_price:
            continue

        item = QuotationItem.objects.create(
            quotation=quotation,
            product_price=product_price,
            service_item=product_price.service_item if product_price.service_item_id else None,
            quantity=candidate['quantity'],
            brand_name=product_price.display_brand_name,
            product_description=product_price.display_description,
            model_number=product_price.display_model_number,
            unit=product_price.display_unit,
            unit_price=product_price.price_without_tax,
            tax_rate=product_price.tax_rate,
            user_brand=_normalized_text(extracted.get('brand')),
            user_name=_normalized_text(extracted.get('user')),
        )
        matched_items.append({
            'requested': candidate['raw'],
            'product_price_id': product_price.pk,
            'model': product_price.display_label,
            'quantity': item.quantity,
        })

    extracted['item_matching'] = {
        'matched_items': matched_items,
        'warnings': warnings,
    }
    if cleared_existing_items and not matched_items:
        quotation.recalculate_totals()
    return warnings


def _heuristic_classification(message):
    text = _message_text(message).lower()
    structured = _parse_structured_fields(message)
    score = 0
    matched_keywords = []
    for keyword in RFQ_KEYWORDS:
        if keyword in text:
            score += 1
            matched_keywords.append(keyword)

    sender_email = structured['contact_email'] or _sender_email(message)
    if sender_email and any(token in text for token in ['qty', 'quantity', 'spec', 'model', 'delivery', 'address of the delivery', '联系方式']):
        score += 1

    for field in ['brand', 'user', 'procurement']:
        if structured[field]:
            score += 1

    is_rfq = score >= 2
    confidence = min(0.95, 0.35 + (score * 0.15)) if score else 0.05
    if structured['brand'] or structured['procurement']:
        summary = f"Brand {structured['brand'] or '-'} requesting {structured['procurement'] or 'quotation'}"
    else:
        summary = f"Detected keywords: {', '.join(matched_keywords)}" if matched_keywords else 'No clear RFQ keywords detected.'
    return {
        'is_rfq': is_rfq,
        'confidence': confidence,
        'customer_name': structured['brand'],
        'contact_name': structured['sender_name'] or _sender_name(message),
        'contact_email': sender_email,
        'contact_phone': structured['contact_phone'],
        'requested_items': [structured['procurement']] if structured['procurement'] else [],
        'special_notes': '\n'.join(filter(None, [
            f"User: {structured['user']}" if structured['user'] else '',
            f"Procurement: {structured['procurement']}" if structured['procurement'] else '',
            f"Delivery: {structured['address']}" if structured['address'] else '',
            _normalized_text(message.body_preview or message.subject)[:500],
        ])),
        'reply_draft': '',
        'summary': summary,
    }


def _definite_non_rfq(message, heuristic=None):
    """Return ``(skip, reason)`` for mail that cannot be an RFQ.

    Headers are read before text because the sending server writes them about
    itself: `Auto-Submitted`, `X-Autoreply` and `List-Unsubscribe` mark bulk or
    machine-generated traffic and a customer's prose cannot imitate them, while a
    signature match can be coincidental. `Auto-Submitted` only counts when its
    value states automation (`auto-replied`, `auto-generated`); the RFC allows
    `manual` for human mail, and treating its presence as a skip would drop a
    real RFQ from a well-configured sender.

    A text signature therefore suppresses a call only when the heuristic found
    no RFQ either. A customer who quotes a newsletter footer, or whose signature
    block says `do not reply`, is still a customer asking for a price - and a
    skipped RFQ is a quotation nobody drafted, which is a worse failure than the
    tokens it would have cost.

    The reason is returned rather than swallowed because it is persisted in
    `rfq_extracted_data`: it is what lets an operator audit a wrong skip, and
    the only evidence that the gate is saving anything.
    """
    metadata = getattr(message, 'metadata', None)
    if isinstance(metadata, dict):
        if str(metadata.get('Auto-Submitted') or '').strip().lower().startswith('auto'):
            return True, 'auto_submitted_header'
        if str(metadata.get('X-Autoreply') or '').strip().lower() in ('yes', 'true'):
            return True, 'autoreply_header'
        if str(metadata.get('List-Unsubscribe') or '').strip():
            return True, 'list_unsubscribe_header'

    if (heuristic or {}).get('is_rfq'):
        return False, ''
    match = _NON_RFQ_SIGNATURES.search(_message_text(message))
    if match:
        return True, f'non_rfq_signature:{match.group(0).lower()}'
    return False, ''


def _sender_is_authorized_rfq_sender(message, extracted=None):
    """Whether the sender is a whitelisted RFQ contact at a known company.

    An unidentified sender counts as unauthorised: the whitelist means nothing
    until the customer behind the address is known.
    """
    extracted = extracted or {}
    contact_email = (_recognized_sender(message)['email'] or extracted.get('contact_email') or _sender_email(message)).strip().lower()
    if not contact_email:
        return False
    company = _resolve_customer(message, extracted)
    if not company:
        return False
    return bool(_resolve_authorized_sender_contact(company, message, extracted))


def _should_skip_llm(message, heuristic):
    """Return ``(skip, reason)`` for a mail that must not be paid for.

    The header rules are always on and cost nothing: machine-generated mail
    cannot hide the headers a sending server writes about itself. A text
    signature is honoured only when the heuristic also found no RFQ, and
    requiring a whitelisted sender is opt-in
    (`RFQ_LLM_REQUIRE_AUTHORIZED_SENDER`) because either rule's false negative is
    a real quotation nobody drafted: a new contact at an existing customer is
    silently dropped, which is business-visible rather than merely cheap.
    """
    skip, reason = _definite_non_rfq(message, heuristic)
    if skip:
        return True, reason
    if settings.RFQ_LLM_REQUIRE_AUTHORIZED_SENDER and not _sender_is_authorized_rfq_sender(message, heuristic):
        return True, 'sender_not_authorized'
    return False, ''


def _call_llm_classify(message):
    system_prompt = (
        '你是一个负责企业采购邮件识别与报价回复草拟的助手。'
        '请判断邮件是否属于客户询价/RFQ，并仅返回一个 JSON 对象，不要返回 Markdown，不要返回解释。'
        'reply_draft 必须使用简体中文，语气专业、简洁、适合作为报价邮件回复草稿。'
        '如果信息不足，也要尽量提取已知字段，并在 summary 中说明。'
    )
    user_prompt = {
        'instruction': '请分析下面的邮件，并返回 JSON。',
        'required_fields': {
            'is_rfq': 'boolean',
            'confidence': '0到1之间的小数',
            'customer_name': '客户名称或品牌代码',
            'contact_name': '联系人/使用人',
            'contact_email': '联系人邮箱',
            'contact_phone': '联系人电话',
            'requested_items': ['请求采购的商品或数量信息'],
            'special_notes': '补充说明，纯文本',
            'reply_draft': '简体中文报价回复草稿',
            'summary': '简短中文摘要',
        },
        'email': {
            'subject': message.subject or '',
            'sender': message.sender or '',
            'recipients': message.recipients or '',
            'body_text': message.body_text or message.body_preview or '',
        },
    }
    result = call_llm_json(
        system_prompt,
        user_prompt,
        purpose=LlmCallRecord.Purpose.RFQ_CLASSIFICATION,
        message=message,
    )
    return result.payload


def classify_rfq_email(message):
    heuristic = _heuristic_classification(message)

    skip, reason = _should_skip_llm(message, heuristic)
    if skip:
        record_skipped_call(LlmCallRecord.Purpose.RFQ_CLASSIFICATION, message=message, reason=reason)
        skipped = heuristic.copy()
        skipped.update({'is_rfq': False, 'llm_skipped': True, 'llm_skip_reason': reason})
        return skipped

    llm_result = _call_llm_classify(message)
    if not llm_result:
        return heuristic

    result = heuristic.copy()
    for key in ['customer_name', 'contact_name', 'contact_email', 'contact_phone', 'requested_items', 'special_notes', 'reply_draft', 'summary']:
        value = llm_result.get(key)
        if value:
            result[key] = value

    if 'is_rfq' in llm_result:
        result['is_rfq'] = bool(llm_result.get('is_rfq'))
    if 'confidence' in llm_result:
        try:
            result['confidence'] = float(llm_result.get('confidence'))
        except (TypeError, ValueError):
            pass
    return result


def _resolve_customer(message, extracted):
    contact_email = (_recognized_sender(message)['email'] or extracted.get('contact_email') or _sender_email(message)).strip().lower()
    customer_name = _normalized_text(extracted.get('customer_name'))
    sender_domain = contact_email.split('@', 1)[1] if '@' in contact_email else ''

    if contact_email:
        company = Company.objects.filter(
            Q(email__iexact=contact_email)
            | Q(company_users__work_email__iexact=contact_email)
            | Q(company_users__user__email__iexact=contact_email)
        ).distinct().first()
        if company:
            return company

    if customer_name:
        company = Company.objects.filter(code__iexact=customer_name).first()
        if company:
            return company
        company = Company.objects.filter(name__iexact=customer_name).first()
        if company:
            return company

    if sender_domain:
        company = Company.objects.filter(email__iendswith='@' + sender_domain).first()
        if company:
            return company
        company = Company.objects.filter(company_users__work_email__iendswith='@' + sender_domain).distinct().first()
        if company:
            return company
        company = Company.objects.filter(company_users__user__email__iendswith='@' + sender_domain).distinct().first()
        if company:
            return company

    return None


def _resolve_company_contact(company, extracted):
    contact_email = (extracted.get('contact_email') or '').strip().lower()
    contact_name = _normalized_text(extracted.get('contact_name'))
    if not company:
        return None
    if contact_email:
        membership = company.company_users.filter(
            Q(work_email__iexact=contact_email) | Q(user__email__iexact=contact_email)
        ).select_related('user').first()
        if membership:
            return membership
    if contact_name:
        membership = company.company_users.filter(name__iexact=contact_name).select_related('user').first()
        if membership:
            return membership
    return company.primary_contact_company_user


def _resolve_authorized_sender_contact(company, message, extracted):
    sender = _recognized_sender(message)
    contact_email = (sender['email'] or extracted.get('contact_email') or '').strip().lower()
    if not company or not contact_email:
        return None
    return company.company_users.filter(
        is_authorized_rfq_sender=True,
    ).filter(
        Q(work_email__iexact=contact_email) | Q(user__email__iexact=contact_email)
    ).select_related('user').first()


def _build_quotation_notes(message, extracted):
    lines = [
        'RFQ Draft generated from mailbox email.',
        f"Source email subject: {message.subject or '-'}",
        f"Source email sender: {message.sender or '-'}",
    ]
    summary = _normalized_text(extracted.get('summary'))
    if summary:
        lines.append(f'Summary: {summary}')
    special_notes = _normalized_text(extracted.get('special_notes'))
    if special_notes:
        lines.append('')
        lines.append('Requested details:')
        lines.append(special_notes)
    item_matching = extracted.get('item_matching') or {}
    warnings = item_matching.get('warnings') or []
    if warnings:
        lines.append('')
        lines.append('Item matching warnings:')
        for warning in warnings:
            requested = warning.get('requested') or '-'
            warning_type = warning.get('type') or 'warning'
            lines.append(f'- {requested}: {warning_type}')
    return '\n'.join(lines)


def _default_reply_body(message, quotation, extracted):
    reply_draft = _normalized_text(extracted.get('reply_draft'))
    if reply_draft and _looks_like_chinese_reply(reply_draft):
        return reply_draft
    contact_name = _recognized_sender(message)['name'] or _normalized_text(extracted.get('contact_name')) or '客户'
    return (
        f"尊敬的{contact_name}：\n\n"
        f"您好，感谢您的询价。现附上报价单 {quotation.quotation_number} 供您参考。\n\n"
        "如需调整配置、数量或送货信息，请随时回复邮件告知，我们会尽快更新。\n\n"
        "谢谢。"
    )


def _looks_like_chinese_reply(value):
    text = _normalized_text(value)
    if not text:
        return False
    if re.search(r'[\u4e00-\u9fff]', text) and not re.search(r'\bDear\b|\bBest Regards\b', text, flags=re.IGNORECASE):
        return True
    return False


def _draft_reply_dispatch(message, quotation, extracted):
    sent_to = (_recognized_sender(message)['email'] or extracted.get('contact_email') or _sender_email(message) or quotation.attn_email or '').strip()
    if not sent_to:
        return None
    subject = message.subject or f'Quotation {quotation.quotation_number}'
    if not subject.lower().startswith('re:'):
        subject = f'Re: {subject}'
    dispatch, _created = EmailDispatch.objects.update_or_create(
        quotation=quotation,
        source_email_message=message,
        status=EmailDispatch.DispatchStatus.DRAFT,
        defaults={
            'subject': subject,
            'body': _default_reply_body(message, quotation, extracted),
            'sent_to': sent_to,
            'cc': '',
            'bcc': '',
            'reply_message_id': message.message_id or '',
            'reply_references': message.message_id or '',
            'created_by': message.mailbox.user,
        },
    )
    return dispatch


def _create_or_update_draft_quotation(message, extracted):
    customer = _resolve_customer(message, extracted)
    if not customer:
        return None

    sender = _recognized_sender(message)
    authorized_sender = _resolve_authorized_sender_contact(customer, message, extracted)
    if not authorized_sender:
        return None

    contact_name = sender['name'] or authorized_sender.get_contact_name()
    contact_phone = _normalized_text(extracted.get('contact_phone')) or authorized_sender.get_contact_phone()
    contact_email = sender['email'] or authorized_sender.get_contact_email()

    quotation, _created = Quotation.objects.update_or_create(
        source_email_message=message,
        defaults={
            'customer': customer,
            'quotation_date': timezone.localdate(),
            'valid_until': timezone.localdate() + timedelta(days=30),
            'attn': contact_name,
            'tel': contact_phone,
            'attn_email': contact_email,
            'status': Quotation.QuotationStatus.DRAFT,
            'requires_confirmation': True,
            'notes': '',
        },
    )
    _sync_quotation_items_from_rfq(quotation, extracted)
    quotation.notes = _build_quotation_notes(message, extracted)
    quotation.save(update_fields=['notes', 'updated_at'])
    return quotation


def process_rfq_message(message):
    if message.direction != message.MessageDirection.INBOX:
        return None

    try:
        extracted = classify_rfq_email(message)
        confidence = extracted.get('confidence')
        if confidence is not None:
            try:
                confidence_value = Decimal(str(round(float(confidence), 2)))
            except (ArithmeticError, TypeError, ValueError):
                confidence_value = None
        else:
            confidence_value = None

        message.rfq_confidence = confidence_value
        message.rfq_summary = extracted.get('summary', '') or ''
        message.rfq_extracted_data = extracted
        message.rfq_error = ''
        message.rfq_processed_at = timezone.now()

        if not extracted.get('is_rfq'):
            message.rfq_status = message.RFQStatus.CLASSIFIED_NON_RFQ
            message.save(update_fields=['rfq_confidence', 'rfq_summary', 'rfq_extracted_data', 'rfq_error', 'rfq_processed_at', 'rfq_status', 'synced_at'])
            return None

        customer = _resolve_customer(message, extracted)
        if customer and not _resolve_authorized_sender_contact(customer, message, extracted):
            message.rfq_status = message.RFQStatus.CLASSIFIED_RFQ
            message.rfq_error = 'RFQ sender is not authorized for automatic quotation generation.'
            message.rfq_summary = extracted.get('summary', '') or 'Likely RFQ detected, but sender authorization is required before auto-generation.'
            message.save(update_fields=['rfq_confidence', 'rfq_summary', 'rfq_extracted_data', 'rfq_error', 'rfq_processed_at', 'rfq_status', 'synced_at'])
            return None

        quotation = _create_or_update_draft_quotation(message, extracted)
        message.rfq_status = message.RFQStatus.QUOTATION_DRAFTED if quotation else message.RFQStatus.CLASSIFIED_RFQ
        message.save(update_fields=['rfq_confidence', 'rfq_summary', 'rfq_extracted_data', 'rfq_error', 'rfq_processed_at', 'rfq_status', 'synced_at'])
        return quotation
    except Exception as exc:
        LOGGER.exception('RFQ processing failed for mailbox message %s', message.pk)
        message.rfq_status = message.RFQStatus.CLASSIFICATION_FAILED
        message.rfq_error = str(exc)
        message.rfq_processed_at = timezone.now()
        message.save(update_fields=['rfq_status', 'rfq_error', 'rfq_processed_at', 'synced_at'])
        return None


def process_pending_rfq_messages(mailbox_settings):
    queryset = mailbox_settings.received_messages.filter(
        direction=mailbox_settings.received_messages.model.MessageDirection.INBOX,
        rfq_status__in=[
            mailbox_settings.received_messages.model.RFQStatus.UNREVIEWED,
            mailbox_settings.received_messages.model.RFQStatus.CLASSIFICATION_FAILED,
            mailbox_settings.received_messages.model.RFQStatus.CLASSIFIED_RFQ,
        ],
    ).order_by('-received_at', '-id')
    processed = 0
    for message in queryset:
        process_rfq_message(message)
        processed += 1
    return processed