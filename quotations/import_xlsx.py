"""Parse externally-issued quotation workbooks and commit them as quotations.

The layout is label-driven rather than positional: real HengJi quotation files
shift the header row and the item count from file to file.
"""
import hashlib
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zipfile import BadZipFile

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException
from django.db import transaction
from django.utils.translation import gettext as _

from assets.models import AssetBrand, AssetModel
from companies.models import Company, ImportRunChange
from products.models import ProductPrice, ServiceItem
from utils.import_rollback import (
    finalize_import_run,
    record_import_change,
    snapshot_instance,
    start_import_run,
)

from .models import Quotation, QuotationItem


IMPORT_STATUS_CHOICES = (
    Quotation.QuotationStatus.DRAFT,
    Quotation.QuotationStatus.SENT,
)


MAX_IMPORT_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 15 * 1024 * 1024
MAX_ROWS_PER_FILE = 200
MAX_TOTAL_ROWS = 500

IMPORT_PREVIEW_SESSION_KEY = 'quotations_excel_import_preview'
IMPORT_MODULE = 'quotations'
IMPORT_TYPE = 'excel_quotation'
PREVIEW_VERSION = 1

MONEY_QUANT = Decimal('0.01')
RECONCILE_TOLERANCE = Decimal('0.02')
DEFAULT_TAX_RATE = Decimal('13.00')
MAX_CANDIDATES = 5
HEADER_SCAN_LIMIT = 200
LABEL_VALUE_REACH = 4

XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Ordered: '含税单价' is a substring of '不含税单价', so each canonical claims the
# first still-unassigned column and the wider names must be tried first.
HEADER_ALIASES = {
    'sequence': ['序列号', '序号', 'no.', 'no', '#', 'item'],
    'brand': ['产品品牌', '品牌', 'brand'],
    'description': ['产品描述', '产品描述及规格', '描述', 'description', 'item description'],
    'user_brand': ['采购方品牌', '客户品牌', 'user brand', 'customer brand'],
    'user_name': ['采购方用户', '采购方用户/项目', '客户用户', '用户', 'user', 'customer user'],
    'unit': ['单位', 'unit'],
    'price_without_tax': ['不含税单价', '未税单价', '单价(不含税)', 'price without tax', 'unit price without tax'],
    'price_with_tax': ['含税单价', '单价(含税)', 'price with tax', 'unit price with tax'],
    'quantity': ['数量', 'qty', 'quantity'],
    'line_total_without_tax': ['不含税总价', '总价(不含税)', 'total without tax', 'amount without tax'],
    'line_total_with_tax': ['含税总价', '总价(含税)', 'total with tax', 'amount with tax'],
}
REQUIRED_COLUMNS = ('description', 'price_without_tax', 'quantity', 'unit')

# Supplier-side labels (From:/MP:/E-Mail:) are deliberately absent: rows 8-10
# carry HengJi's own contact details in columns G/I, not the customer's.
LABEL_FIELDS = {
    'to': 'customer_name',
    'attn': 'attn',
    'attention': 'attn',
    'tel': 'tel',
    'telephone': 'tel',
    'quote date': 'quotation_date',
    'date': 'fallback_date',
    'validate date': 'valid_until',
    'valid until': 'valid_until',
    'valid date': 'valid_until',
}

STOP_MARKERS = (
    '不含税总价', 'total price without vat', '含税', 'total price including',
    '税金', 'vat(', 'remark', 'confirmed by', 'prepared by', 'signature',
)
SERVICE_UNITS = {'项', '次', '式', 'job'}


def _norm(value):
    return ' '.join(str(value if value is not None else '').split()).strip().lower()


def _text(value):
    return ' '.join(str(value if value is not None else '').split()).strip()


def _label(value):
    return _norm(value).rstrip(':：').strip()


def _money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = _norm(value)
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _norm(value)
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _last_numeric(values):
    result = None
    for value in values:
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            result = value
    return result


def _value_right(values, index):
    for offset in range(1, LABEL_VALUE_REACH + 1):
        position = index + offset
        if position >= len(values):
            break
        if values[position] is not None and str(values[position]).strip():
            return values[position]
    return None


def validate_upload_files(files):
    """Extension/size/count gate. Content is validated by the parser."""
    errors = []
    if not files:
        return [_('Select at least one .xlsx file.')]
    if len(files) > MAX_IMPORT_FILES:
        errors.append(_('Too many files. Maximum is %(count)s per batch.') % {'count': MAX_IMPORT_FILES})
    total = 0
    for uploaded in files:
        name = os.path.basename(uploaded.name or '')
        lowered = name.lower()
        if lowered.endswith('.xls'):
            errors.append(_('%(name)s: only .xlsx files are supported. Legacy .xls must be re-saved as .xlsx.') % {'name': name})
        elif not lowered.endswith('.xlsx'):
            errors.append(_('%(name)s: only .xlsx files are supported.') % {'name': name})
        size = getattr(uploaded, 'size', 0) or 0
        if size == 0:
            errors.append(_('%(name)s: the file is empty.') % {'name': name})
        elif size > MAX_FILE_BYTES:
            errors.append(_('%(name)s: file too large. Maximum size is 10MB per file.') % {'name': name})
        total += size
    if total > MAX_BATCH_BYTES:
        errors.append(_('Batch too large. Maximum total size is 15MB.'))
    return errors


def _resolve_columns(values):
    normalized = [_norm(value) for value in values]
    columns = {}
    claimed = set()
    for canonical, aliases in HEADER_ALIASES.items():
        for index, cell in enumerate(normalized):
            if index in claimed or not cell:
                continue
            if any(cell == _norm(alias) or _norm(alias) in cell for alias in aliases):
                columns[canonical] = index
                claimed.add(index)
                break
    return columns


def _find_header(workbook):
    partial = None
    for sheet in workbook.worksheets:
        if sheet.sheet_state != 'visible':
            continue
        limit = min(sheet.max_row or 0, HEADER_SCAN_LIMIT)
        for row_index, values in enumerate(
            sheet.iter_rows(min_row=1, max_row=limit, values_only=True), start=1
        ):
            columns = _resolve_columns(values)
            if 'description' not in columns or 'price_without_tax' not in columns:
                continue
            missing = [name for name in REQUIRED_COLUMNS if name not in columns]
            if not missing:
                return sheet, row_index, columns
            if partial is None:
                partial = missing
    if partial:
        raise ValueError(_('Missing required column: %(column)s.') % {'column': ', '.join(partial)})
    raise ValueError(_('No quotation header row found (expected 产品描述 and 不含税单价 columns).'))


def _scan_labels(sheet, header_row):
    found = {}
    for values in sheet.iter_rows(min_row=1, max_row=header_row - 1, values_only=True):
        for index, cell in enumerate(values):
            field = LABEL_FIELDS.get(_label(cell))
            if not field or field in found:
                continue
            raw = _value_right(values, index)
            if raw is None:
                continue
            if field in ('quotation_date', 'valid_until', 'fallback_date'):
                parsed = _date(raw)
                if parsed is None:
                    continue
                found[field] = parsed.isoformat()
            else:
                found[field] = _text(raw)
    return found


def derive_tax_rate(unit_price, price_with_tax, label_rate):
    """Per-line rate from the incl-tax/ex-tax ratio; the VAT label is a cross-check."""
    ex_tax = Decimal(unit_price)
    incl_tax = Decimal(price_with_tax) if price_with_tax else None
    label = Decimal(label_rate) if label_rate else None
    if ex_tax > 0 and incl_tax is not None:
        rate = ((incl_tax - ex_tax) / ex_tax * 100).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        if Decimal('0') <= rate <= Decimal('100'):
            if label is not None and abs(rate - label) > MONEY_QUANT:
                return str(rate), _('Tax rate %(rate)s%% differs from VAT label %(label)s%%.') % {
                    'rate': rate, 'label': label,
                }
            return str(rate), None
    if label is not None and Decimal('0') <= label <= Decimal('100'):
        return str(label.quantize(MONEY_QUANT)), _('Tax rate taken from VAT label (no incl-tax price).')
    return str(DEFAULT_TAX_RATE), _('Tax rate defaulted to 13%%.')


def _scan_footer(sheet, start_row, columns):
    totals = {'without_tax': None, 'tax': None, 'with_tax': None}
    label_rate = None
    remarks = ''
    for values in sheet.iter_rows(min_row=start_row, max_row=sheet.max_row, values_only=True):
        if not values:
            continue
        text = _norm(values[0])
        if not text:
            continue
        amount = _money(_last_numeric(values))
        # '不含税总价' also contains '含税', and the incl-tax label carries VAT(x%)
        # too, so the ex-tax and incl-tax branches must be tested before the tax one.
        if '不含税总价' in text or 'total price without vat' in text:
            totals['without_tax'] = str(amount) if amount is not None else None
        elif '含税' in text or 'total price including' in text:
            totals['with_tax'] = str(amount) if amount is not None else None
        elif '税金' in text or 'vat(' in text:
            totals['tax'] = str(amount) if amount is not None else None
        elif _label(values[0]) == 'remark':
            for cell in values[1:]:
                if cell is not None and str(cell).strip():
                    remarks = str(cell).strip()
                    break
        match = re.search(r'vat\((\d+(?:\.\d+)?)%\)', text)
        if match and label_rate is None:
            label_rate = match.group(1)
    return totals, label_rate, remarks


def parse_quotation_workbook(uploaded_file):
    """Read one quotation sheet into a JSON-serializable dict (no match info)."""
    uploaded_file.seek(0)
    try:
        workbook = openpyxl.load_workbook(uploaded_file, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError):
        raise ValueError(_('Not a valid .xlsx workbook.'))

    sheet, header_row, columns = _find_header(workbook)
    labels = _scan_labels(sheet, header_row)

    rows = []
    errors = []
    warnings = []
    stop_row = sheet.max_row + 1
    for row_index, values in enumerate(
        sheet.iter_rows(min_row=header_row + 1, max_row=sheet.max_row, values_only=True),
        start=header_row + 1,
    ):
        first = _norm(values[0]) if values else ''
        if first and any(marker in first for marker in STOP_MARKERS):
            stop_row = row_index
            break
        if all(value is None or not str(value).strip() for value in values):
            continue

        def cell(name):
            index = columns.get(name)
            return values[index] if index is not None and index < len(values) else None

        description = _text(cell('description'))
        if not description:
            warnings.append(_('Unrecognized row %(row)s skipped.') % {'row': row_index})
            continue
        if len(rows) >= MAX_ROWS_PER_FILE:
            errors.append(_('Too many lines in this file. Maximum is %(count)s.') % {'count': MAX_ROWS_PER_FILE})
            break

        quantity = _integer(cell('quantity'))
        unit_price = _money(cell('price_without_tax'))
        if quantity is None or quantity < 1:
            errors.append(_('Invalid quantity on row %(row)s.') % {'row': row_index})
            continue
        if unit_price is None or unit_price < 0:
            errors.append(_('Invalid price on row %(row)s.') % {'row': row_index})
            continue

        rows.append({
            'source_row': row_index,
            'sequence': _integer(cell('sequence')) or len(rows) + 1,
            'brand': _text(cell('brand')),
            'description': description,
            'user_brand': _text(cell('user_brand')),
            'user_name': _text(cell('user_name')),
            'unit': _text(cell('unit')),
            'quantity': quantity,
            'unit_price': str(unit_price),
            'price_with_tax': str(_money(cell('price_with_tax'))) if _money(cell('price_with_tax')) is not None else None,
            'source_line_without_tax': str(_money(cell('line_total_without_tax'))) if _money(cell('line_total_without_tax')) is not None else None,
            'source_line_with_tax': str(_money(cell('line_total_with_tax'))) if _money(cell('line_total_with_tax')) is not None else None,
        })

    totals, label_rate, remarks = _scan_footer(sheet, stop_row, columns)

    if not rows and not errors:
        errors.append(_('No quotation lines were found below the header row.'))

    codes = []
    for row in rows:
        if row['user_brand'] and row['user_brand'] not in codes:
            codes.append(row['user_brand'])
    if len(codes) > 1:
        warnings.append(_('Multiple customer codes found (%(codes)s); using the first.') % {'codes': ', '.join(codes)})

    quotation_date = labels.get('quotation_date') or labels.get('fallback_date')
    valid_until = labels.get('valid_until')
    if not quotation_date:
        warnings.append(_('No valid date found; using fallback.'))
    if not valid_until:
        warnings.append(_('No valid date found; using fallback.'))

    return {
        'sheet_name': sheet.title,
        'header_row': header_row,
        'customer_name': labels.get('customer_name', ''),
        'attn': labels.get('attn', ''),
        'tel': labels.get('tel', ''),
        'codes': codes,
        'quotation_date': quotation_date,
        'valid_until': valid_until,
        'remarks': remarks,
        'totals': totals,
        'label_rate': label_rate,
        'rows': rows,
        'warnings': warnings,
        'errors': errors,
    }


def resolve_customer(customer_name, codes):
    """Code (column D) -> exact legal name (B8) -> normalized name."""
    active = Company.objects.filter(status=Company.CompanyStatus.ACTIVE)
    for code in codes:
        hit = active.filter(code__iexact=code).first()
        if hit:
            return hit.pk, 'code'
    name = _text(customer_name)
    if name:
        hit = active.filter(name__iexact=name).first()
        if hit:
            return hit.pk, 'name'

        def normalized(value):
            return ' '.join(value.split()).replace('（', '(').replace('）', ')').lower()

        target = normalized(name)
        for company in active:
            if normalized(company.name) == target:
                return company.pk, 'normalized_name'
    return None, None


def catalog_prices():
    return list(
        ProductPrice.objects.filter(is_current=True)
        .select_related('brand', 'model', 'model__category', 'service_item')
        .order_by('service_item__name', 'brand__name', 'model__name')
    )


def serialize_catalog(prices):
    return [{
        'product_price_id': price.pk,
        'label': price.display_label,
        'catalog_type': price.catalog_type,
        'brand': price.display_brand_name,
        'model_number': price.display_model_number,
        'unit': price.display_unit,
        'price_without_tax': str(price.price_without_tax),
        'tax_rate': str(price.tax_rate),
    } for price in prices]


def _model_tokens(description):
    from accounts.rfq_ai import _extract_model_tokens
    return _extract_model_tokens(description)


def match_row_candidates(row, prices):
    """Deterministic two-tier match. Never calls the LLM disambiguator."""
    from accounts.rfq_ai import (
        _collect_catalog_text_matches,
        _normalized_text,
        _product_price_searchable_values,
    )

    description = row['description']
    matches = []
    confidence = 'none'
    for token in row['model_tokens']:
        hits = [
            price for price in prices
            if price.model_id and _normalized_text(price.model.model_number).lower() == token.lower()
        ]
        if hits:
            matches = hits
            confidence = 'model_number'
            break

    if not matches:
        hits = _collect_catalog_text_matches(
            prices, _normalized_text(description).lower(), _product_price_searchable_values
        )
        if not hits and row['brand']:
            hits = _collect_catalog_text_matches(
                prices,
                _normalized_text(f"{row['brand']} {description}").lower(),
                _product_price_searchable_values,
            )
        if hits:
            matches = hits
            confidence = 'text'

    brand = _normalized_text(row['brand']).lower()
    if brand and matches:
        matches = sorted(
            matches,
            key=lambda price: 0 if _normalized_text(price.display_brand_name).lower() == brand else 1,
        )

    candidates = serialize_catalog(matches[:MAX_CANDIDATES])
    return {
        'confidence': confidence,
        'candidates': candidates,
        'suggested_id': candidates[0]['product_price_id'] if len(matches) == 1 else None,
    }


def build_create_defaults(row):
    is_service = not row['brand'] and _norm(row['unit']) in SERVICE_UNITS
    tokens = row['model_tokens']
    return {
        'catalog_type': 'service' if is_service else 'hardware',
        'brand_name': row['brand'],
        'model_name': row['description'][:255],
        'model_number': (tokens[0] if tokens else '')[:100],
        'service_name': row['description'][:255],
        'service_group': '',
        'unit': row['unit'],
    }


def _line_amounts(row):
    ex_tax = Decimal(row['unit_price']) * row['quantity']
    tax = ex_tax * (Decimal(row['tax_rate']) / Decimal('100'))
    return ex_tax.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP), tax.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def build_preview_payload(files, default_status):
    """Validate -> dedup -> parse -> resolve -> match -> reconcile, for the session."""
    payload = {
        'version': PREVIEW_VERSION,
        'default_status': default_status,
        'files': [],
        'duplicates': [],
        'batch_warnings': [],
        'batch_errors': [],
    }
    seen = {}
    total_rows = 0
    prices = catalog_prices()

    for index, uploaded in enumerate(files):
        filename = os.path.basename(uploaded.name or f'file{index}.xlsx')
        uploaded.seek(0)
        digest = hashlib.sha256(uploaded.read()).hexdigest()
        uploaded.seek(0)
        if digest in seen:
            payload['duplicates'].append({'filename': filename, 'duplicate_of': seen[digest]})
            continue
        seen[digest] = filename

        file_id = f'f{len(payload["files"])}'
        entry = {
            'file_id': file_id,
            'filename': filename,
            'sha256': digest,
            'external_ref': os.path.splitext(filename)[0],
            'sheet_name': '',
            'header_row': 0,
            'customer': {'name': '', 'code': '', 'codes': [], 'attn': '', 'tel': ''},
            'customer_id': None,
            'customer_match': None,
            'quotation_date': None,
            'valid_until': None,
            'remarks': '',
            'status': default_status,
            'totals': {'without_tax': None, 'tax': None, 'with_tax': None, 'label_rate': None},
            'computed_totals': {'without_tax': '0.00', 'tax': '0.00', 'with_tax': '0.00'},
            'reconciliation_ok': True,
            'warnings': [],
            'errors': [],
            'rows': [],
        }
        payload['files'].append(entry)

        try:
            parsed = parse_quotation_workbook(uploaded)
        except ValueError as exc:
            entry['errors'].append(str(exc))
            continue

        entry['sheet_name'] = parsed['sheet_name']
        entry['header_row'] = parsed['header_row']
        entry['customer'] = {
            'name': parsed['customer_name'],
            'code': parsed['codes'][0] if parsed['codes'] else '',
            'codes': parsed['codes'],
            'attn': parsed['attn'],
            'tel': parsed['tel'],
        }
        entry['quotation_date'] = parsed['quotation_date']
        entry['valid_until'] = parsed['valid_until']
        entry['remarks'] = parsed['remarks']
        entry['totals'] = dict(parsed['totals'], label_rate=parsed['label_rate'])
        entry['warnings'].extend(parsed['warnings'])
        entry['errors'].extend(parsed['errors'])

        company_id, match_kind = resolve_customer(parsed['customer_name'], parsed['codes'])
        entry['customer_id'] = company_id
        entry['customer_match'] = match_kind
        if company_id is None:
            entry['warnings'].append(_('Customer not matched — select a company manually.'))

        if Quotation.objects.filter(notes__contains=filename).exists():
            entry['warnings'].append(_('This file appears to have been imported before.'))

        computed_ex = Decimal('0.00')
        computed_tax = Decimal('0.00')
        for row_index, raw in enumerate(parsed['rows']):
            rate, rate_warning = derive_tax_rate(raw['unit_price'], raw['price_with_tax'], parsed['label_rate'])
            row = dict(raw)
            row['row_id'] = f'{file_id}-r{row_index}'
            row['model_tokens'] = _model_tokens(raw['description'])
            row['tax_rate'] = rate
            row['warnings'] = [rate_warning] if rate_warning else []
            row['errors'] = []
            ex_tax, tax = _line_amounts(row)
            row['line_total_without_tax'] = str(ex_tax)
            row['line_total_with_tax'] = str((ex_tax + tax).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))
            computed_ex += ex_tax
            computed_tax += tax
            row['match'] = match_row_candidates(row, prices)
            row['create_defaults'] = build_create_defaults(row)
            if not row['brand']:
                row['warnings'].append(_('No brand in the source file.'))
            entry['rows'].append(row)

        total_rows += len(entry['rows'])
        entry['computed_totals'] = {
            'without_tax': str(computed_ex.quantize(MONEY_QUANT)),
            'tax': str(computed_tax.quantize(MONEY_QUANT)),
            'with_tax': str((computed_ex + computed_tax).quantize(MONEY_QUANT)),
        }
        for key in ('without_tax', 'tax', 'with_tax'):
            source = entry['totals'].get(key)
            if source is None:
                continue
            if abs(Decimal(source) - Decimal(entry['computed_totals'][key])) > RECONCILE_TOLERANCE:
                entry['reconciliation_ok'] = False
        if not entry['reconciliation_ok']:
            entry['warnings'].append(_('Totals do not match the source file.'))

        if entry['valid_until']:
            try:
                if date.fromisoformat(entry['valid_until']) < date.today():
                    entry['warnings'].append(_('The validity date is already in the past.'))
            except ValueError:
                pass

    if total_rows > MAX_TOTAL_ROWS:
        payload['batch_errors'].append(
            _('Too many lines in this batch. Maximum is %(count)s.') % {'count': MAX_TOTAL_ROWS}
        )
    return payload


def _create_collision(create, row):
    """Return a translated blocker when the target already has a current price."""
    unit = _text(create.get('unit') or row['unit'])
    if not unit:
        return _('Unit is required to create a catalog entry.')
    if create.get('catalog_type') == 'hardware':
        brand_name = _text(create.get('brand_name'))
        model_name = _text(create.get('model_name'))
        model_number = _text(create.get('model_number'))
        if not brand_name or not model_name:
            return _('Brand name and model name are required to create a hardware entry.')
        brand = AssetBrand.objects.filter(name__iexact=brand_name).first()
        if brand is None:
            return None
        if model_number:
            model = AssetModel.objects.filter(brand=brand, model_number__iexact=model_number).first()
        else:
            model = AssetModel.objects.filter(brand=brand, name__iexact=model_name).first()
        if model and ProductPrice.objects.filter(model=model, is_current=True).exists():
            return _('This model already has a current price — map the line to it instead.')
        return None
    service_name = _text(create.get('service_name'))
    if not service_name:
        return _('Service name is required to create a service entry.')
    service = ServiceItem.objects.filter(name__iexact=service_name).first()
    if service and ProductPrice.objects.filter(service_item=service, is_current=True).exists():
        return _('This service already has a current price — map the line to it instead.')
    return None


def validate_confirm_payload(session_payload, post_payload):
    """Re-check every decision against the live DB. Returns (decisions, errors)."""
    if not session_payload or session_payload.get('version') != PREVIEW_VERSION:
        return {}, [_('Import preview expired. Please upload the files again.')]

    errors = list(session_payload.get('batch_errors') or [])
    decisions = {'files': {}}
    submitted = (post_payload or {}).get('files') or {}
    if not isinstance(submitted, dict):
        return {}, errors + [_('Import preview expired. Please upload the files again.')]

    for entry in session_payload.get('files') or []:
        if entry['errors']:
            continue
        choice = submitted.get(entry['file_id']) or {}
        if choice.get('excluded'):
            continue

        label = entry['filename']
        customer_id = choice.get('customer_id') or entry['customer_id']
        customer = Company.objects.filter(
            pk=customer_id, status=Company.CompanyStatus.ACTIVE
        ).first() if customer_id else None
        if customer is None:
            errors.append(_('%(name)s: select a customer for every file before confirming.') % {'name': label})

        status = choice.get('status') or entry['status']
        if status not in IMPORT_STATUS_CHOICES:
            errors.append(_('%(name)s: invalid import status.') % {'name': label})

        row_decisions = {}
        kept = 0
        submitted_rows = choice.get('rows') or {}
        for row in entry['rows']:
            decision = submitted_rows.get(row['row_id']) or {}
            action = decision.get('action')
            where = _('%(file)s row %(row)s') % {'file': label, 'row': row['source_row']}
            if action == 'skip':
                row_decisions[row['row_id']] = {'action': 'skip'}
                continue
            if action == 'map':
                price_id = decision.get('product_price_id')
                if not ProductPrice.objects.filter(pk=price_id, is_current=True).exists():
                    errors.append(_('%(where)s: the selected catalog item is no longer available.') % {'where': where})
                    continue
                row_decisions[row['row_id']] = {'action': 'map', 'product_price_id': int(price_id)}
                kept += 1
                continue
            if action == 'create':
                create = dict(row['create_defaults'])
                create.update({
                    key: _text(value)
                    for key, value in (decision.get('create') or {}).items()
                    if key in create
                })
                collision = _create_collision(create, row)
                if collision:
                    errors.append(f'{where}: {collision}')
                    continue
                row_decisions[row['row_id']] = {'action': 'create', 'create': create}
                kept += 1
                continue
            errors.append(_('%(where)s: every line must be mapped, created, or skipped before confirming.') % {'where': where})

        if kept == 0:
            errors.append(_('All lines in %(name)s are skipped — nothing to import.') % {'name': label})
            continue
        if customer is None or status not in IMPORT_STATUS_CHOICES:
            continue
        decisions['files'][entry['file_id']] = {
            'customer_id': customer.pk,
            'status': status,
            'rows': row_decisions,
        }

    if not decisions['files'] and not errors:
        errors.append(_('No importable files in this batch.'))
    return decisions, errors


def _unique_brand_code(name):
    base = re.sub(r'[^A-Z0-9]', '', name.upper())[:20]
    if not base:
        base = 'B' + hashlib.sha1(name.encode('utf-8')).hexdigest()[:8].upper()
    candidate, suffix = base, 2
    while AssetBrand.objects.filter(code__iexact=candidate).exists():
        candidate = f'{base[:17]}-{suffix}'
        suffix += 1
    return candidate


def _create_catalog_entry(create, row, cache, record):
    """Unit lives on the model/service: ProductPrice.save() forces it from there."""
    unit = (_text(create.get('unit')) or row['unit'])[:50]
    price = Decimal(row['unit_price'])
    tax_rate = Decimal(row['tax_rate'])

    if create['catalog_type'] == 'hardware':
        brand_name = _text(create['brand_name'])[:255]
        model_name = _text(create['model_name'])[:255]
        model_number = _text(create.get('model_number'))[:100]
        key = ('hw', brand_name.lower(), (model_number or model_name).lower())
        if key in cache:
            return cache[key]
        brand = AssetBrand.objects.filter(name__iexact=brand_name).first()
        if brand is None:
            brand = AssetBrand.objects.create(name=brand_name, code=_unique_brand_code(brand_name))
            record(brand)
        if model_number:
            model = AssetModel.objects.filter(brand=brand, model_number__iexact=model_number).first()
        else:
            model = AssetModel.objects.filter(brand=brand, name__iexact=model_name).first()
        if model is None:
            model = AssetModel.objects.create(
                brand=brand, name=model_name, model_number=model_number or None,
                unit=unit, description=row['description'],
            )
            record(model)
        if ProductPrice.objects.filter(model=model, is_current=True).exists():
            raise ValueError(_('This model already has a current price — map the line to it instead.'))
        price_row = ProductPrice.objects.create(
            model=model, price_without_tax=price, tax_rate=tax_rate, is_current=True,
        )
        record(price_row, row['source_row'])
        cache[key] = price_row
        return price_row

    service_name = _text(create['service_name'])[:255]
    key = ('svc', service_name.lower())
    if key in cache:
        return cache[key]
    service = ServiceItem.objects.filter(name__iexact=service_name).first()
    if service is None:
        service = ServiceItem.objects.create(
            name=service_name, service_group=_text(create.get('service_group'))[:255],
            unit=unit or 'JOB', description=row['description'],
        )
        record(service)
    if ProductPrice.objects.filter(service_item=service, is_current=True).exists():
        raise ValueError(_('This service already has a current price — map the line to it instead.'))
    price_row = ProductPrice.objects.create(
        service_item=service, price_without_tax=price, tax_rate=tax_rate, is_current=True,
    )
    record(price_row, row['source_row'])
    cache[key] = price_row
    return price_row


def commit_import(user, session_payload, decisions):
    """Write the whole batch in one transaction with a reverse-deletable ledger."""
    quotations = []
    issues = []
    skipped = len(session_payload.get('duplicates') or [])
    filenames = []
    total_rows = sum(len(entry['rows']) for entry in session_payload['files'])
    processed_rows = 0

    with transaction.atomic():
        run = start_import_run(user, IMPORT_MODULE, IMPORT_TYPE, total_rows=total_rows)
        sequence = 0

        def record(instance, row_number=None):
            nonlocal sequence
            sequence += 1
            record_import_change(
                run, sequence, ImportRunChange.ChangeOperation.CREATE, instance,
                row_number=row_number, after_data=snapshot_instance(instance),
            )

        catalog_cache = {}
        for entry in session_payload['files']:
            decision = decisions['files'].get(entry['file_id'])
            if not decision:
                continue
            filenames.append(entry['filename'])
            issues.extend(entry['warnings'])

            # Catalog CREATEs are recorded before the quotation and its items so
            # rollback_run's reverse-sequence delete never hits product_price PROTECT.
            row_prices = {}
            for row in entry['rows']:
                choice = decision['rows'].get(row['row_id'])
                if not choice or choice['action'] == 'skip':
                    skipped += 1
                    continue
                processed_rows += 1
                if choice['action'] == 'map':
                    row_prices[row['row_id']] = ProductPrice.objects.get(
                        pk=choice['product_price_id'], is_current=True
                    )
                else:
                    row_prices[row['row_id']] = _create_catalog_entry(
                        choice['create'], row, catalog_cache, record
                    )

            customer = Company.objects.get(pk=decision['customer_id'])
            quotation_date = (
                date.fromisoformat(entry['quotation_date']) if entry['quotation_date'] else date.today()
            )
            valid_until = (
                date.fromisoformat(entry['valid_until'])
                if entry['valid_until'] else quotation_date + timedelta(days=30)
            )
            quotation = Quotation.objects.create(
                quotation_number='',
                customer=customer,
                quotation_date=quotation_date,
                valid_until=valid_until,
                attn=entry['customer']['attn'][:200],
                tel=entry['customer']['tel'][:20],
                attn_email='',
                status=decision['status'],
                requires_confirmation=False,
                pdf_template=customer.default_quotation_template,
                remarks=entry['remarks'],
                notes=f"Imported from Excel: {entry['filename']} (external ref: {entry['external_ref']}).",
            )
            record(quotation)
            quotations.append(quotation)

            for row in entry['rows']:
                price = row_prices.get(row['row_id'])
                if price is None:
                    continue
                tokens = row['model_tokens']
                item = QuotationItem(
                    quotation=quotation,
                    product_price=price,
                    # Non-empty brand_name blocks save()'s catalog snapshot overwrite,
                    # which would otherwise replace the Excel price and tax rate.
                    brand_name=(row['brand'] or price.display_brand_name)[:255],
                    product_description=row['description'],
                    model_number=(tokens[0] if tokens else price.display_model_number)[:100],
                    unit=(row['unit'] or price.display_unit)[:50],
                    quantity=row['quantity'],
                    unit_price=Decimal(row['unit_price']),
                    tax_rate=Decimal(row['tax_rate']),
                    user_brand=row['user_brand'][:255],
                    user_name=row['user_name'][:200],
                )
                item.save()
                record(item, row['source_row'])

        finalize_import_run(
            run, created=len(quotations), updated=0, skipped=skipped, error_count=0,
            notes='; '.join(filenames),
        )

    return {
        'run': run,
        'quotations': quotations,
        'created': len(quotations),
        'skipped': skipped,
        'issues': issues,
        'total_rows': total_rows,
        'processed_rows': processed_rows,
    }