"""Services for quotation PDF rendering.

The Excel-template writer that used to live here (``fill_quotation_template`` and
its LibreOffice converter) had no call sites left: quotation PDFs are produced by
``render_quotation_pdf_html`` from an HTML template through WeasyPrint, which
returns bytes and never touches the filesystem. It was deleted rather than
converted, because its output landed in ``MEDIA_ROOT/quotations/generated/``.
"""

from pathlib import Path
from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string

from weasyprint import HTML

from quotations.template_registry import get_quotation_template_definition


def _build_pdf_display_row(item, index):
    unit_price_with_tax = item.unit_price * (Decimal('1.00') + (item.tax_rate / Decimal('100.00')))
    return {
        'index': index,
        'model_number': item.model_number,
        'brand_name': item.brand_name,
        'product_description': item.product_description,
        'user_brand': item.user_brand,
        'user_name': item.user_name,
        'unit': item.unit,
        'unit_price': item.unit_price,
        'unit_price_with_tax': unit_price_with_tax,
        'quantity': item.quantity,
        'line_total_without_tax': item.line_total_without_tax,
        'line_total_with_tax': item.line_total_with_tax,
        'tax_amount': item.tax_amount,
    }


def _build_empty_pdf_display_row(index):
    return {
        'index': index,
        'model_number': '',
        'brand_name': '',
        'product_description': '',
        'user_brand': '',
        'user_name': '',
        'unit': '',
        'unit_price': '',
        'unit_price_with_tax': '',
        'quantity': '',
        'line_total_without_tax': '',
        'line_total_with_tax': '',
        'tax_amount': '',
    }


def _build_pdf_display_rows(items, minimum_rows=1):
    rows = [_build_pdf_display_row(item, index) for index, item in enumerate(items, start=1)]
    while len(rows) < minimum_rows:
        rows.append(_build_empty_pdf_display_row(len(rows) + 1))
    return rows


def _build_pdf_section_totals(items):
    total_without_tax = Decimal('0.00')
    total_tax = Decimal('0.00')
    total_with_tax = Decimal('0.00')

    for item in items:
        total_without_tax += item.line_total_without_tax
        total_tax += item.tax_amount
        total_with_tax += item.line_total_with_tax

    vat_percent = Decimal('0.00')
    if total_without_tax:
        vat_percent = (total_tax / total_without_tax) * Decimal('100.00')

    return {
        'total_without_tax': total_without_tax,
        'total_tax': total_tax,
        'total_with_tax': total_with_tax,
        'vat_percent': vat_percent,
    }


def split_quotation_items_for_pdf(ordered_items):
    hardware_items = [item for item in ordered_items if not getattr(item, 'service_item_id', None)]
    service_items = [item for item in ordered_items if getattr(item, 'service_item_id', None)]
    return {
        'hardware_items': hardware_items,
        'service_items': service_items,
        'hardware_display_rows': _build_pdf_display_rows(hardware_items),
        'service_display_rows': _build_pdf_display_rows(service_items),
        'hardware_totals': _build_pdf_section_totals(hardware_items),
        'service_totals': _build_pdf_section_totals(service_items),
    }


def resolve_quotation_pdf_template(quotation, template_code=None):
    return get_quotation_template_definition(template_code or quotation.pdf_template)


def render_quotation_pdf_html(quotation, template_code=None):
    """Render quotation PDF bytes from Django HTML template (LibreOffice-free path)."""
    ordered_items = list(quotation.items.all().order_by('id'))
    pdf_sections = split_quotation_items_for_pdf(ordered_items)
    template_definition = resolve_quotation_pdf_template(quotation, template_code=template_code)

    vat_percent = Decimal('0.00')
    if quotation.total_without_tax:
        vat_percent = (quotation.total_tax / quotation.total_without_tax) * Decimal('100')

    context = {
        'quotation': quotation,
        'items': ordered_items,
        'vat_percent': vat_percent,
        'template_definition': template_definition,
        'remarks_text': quotation.get_effective_remarks(ordered_items=ordered_items),
        'logo_path': (Path(settings.BASE_DIR) / 'static' / 'images' / 'quotation_template_logo.png').resolve().as_uri(),
        'header_rule_path': (Path(settings.BASE_DIR) / 'static' / 'images' / 'quotation_header_rule.png').resolve().as_uri(),
        'seal_path': (Path(settings.BASE_DIR) / 'static' / 'images' / 'sample_contact_seal.png').resolve().as_uri(),
        'prepared_by_company': settings.COMPANY_NAME_ZH,
    }
    context.update(pdf_sections)
    html = render_to_string(template_definition['template_path'], context)
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
