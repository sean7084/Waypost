"""Tests for importing externally issued quotation workbooks.

The xlsx builder reproduces the real HengJi quotation layout, including the
supplier-side contact block, float noise in the incl-tax column and the
scratch sheets that must be ignored.
"""
import io
import json
import os
import tempfile
from datetime import date, datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from openpyxl import Workbook

from accounts.models import AdminRole, LlmCallRecord
from assets.models import AssetBrand, AssetModel
from companies.models import Company, ImportRun
from products.models import ProductPrice, ServiceItem
from quotations.import_xlsx import (
    IMPORT_PREVIEW_SESSION_KEY,
    MAX_FILE_BYTES,
    build_preview_payload,
    match_row_candidates,
    parse_quotation_workbook,
    catalog_prices,
)
from quotations.models import Quotation, QuotationItem

User = get_user_model()

XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

ROLE_DEFAULTS = {
    'superadmin': ('Superadmin', 'Full access.'),
    'viewer': ('Viewer', 'Read only.'),
    'order_management_specialist': ('Order Management Specialist', 'Order flows.'),
    'order_management_manager': ('Order Management Manager', 'Order approvals.'),
}

BAL_ITEMS = [
    {'brand': 'Dell', 'description': 'E1715S 17寸显示器', 'unit': '台', 'quantity': 1, 'price': '750'},
    {'brand': '', 'description': '显示器支架', 'unit': '个', 'quantity': 1, 'price': '70'},
    {'brand': 'Zebra', 'description': 'DS2208', 'unit': '个', 'quantity': 2, 'price': '448'},
]

# The full 18-line sample: ex-tax 52251.00, VAT 6792.63, incl-tax 59043.63.
FULL_BAL_ITEMS = [
    {'brand': 'Dell', 'description': 'E1715S 17寸显示器', 'unit': '台', 'quantity': 1, 'price': '750'},
    {'brand': '', 'description': '显示器支架', 'unit': '个', 'quantity': 1, 'price': '70'},
    {'brand': 'Zebra', 'description': 'DS2208', 'unit': '个', 'quantity': 1, 'price': '448'},
    {'brand': 'Epson', 'description': 'TM-T88VI', 'unit': '台', 'quantity': 1, 'price': '1717'},
    {'brand': '公牛', 'description': '抗电涌插排插座 6位总控 超功率保护 3米 10A 250V', 'unit': '个', 'quantity': 1, 'price': '90'},
    {'brand': '浩顺', 'description': '钱箱', 'unit': '个', 'quantity': 1, 'price': '120'},
    {'brand': 'HP ', 'description': 'OfficeJet Pro 9130', 'unit': '台', 'quantity': 1, 'price': '2260'},
    {'brand': '浩客', 'description': 'Hawk S760 7口USB充电器', 'unit': '个', 'quantity': 1, 'price': '180'},
    {'brand': 'Zebra', 'description': 'ZD421', 'unit': '台', 'quantity': 1, 'price': '3372'},
    {'brand': 'Apple', 'description': 'iPad A16 128GB WLAN版', 'unit': '台', 'quantity': 3, 'price': '3698'},
    {'brand': 'Apple', 'description': 'iPad cover', 'unit': '套', 'quantity': 3, 'price': '580'},
    {'brand': 'Apple', 'description': 'iPhone 17e 256G', 'unit': '台', 'quantity': 1, 'price': '5180'},
    {'brand': 'Zebra', 'description': 'RFD40 Premium Plus', 'unit': '台', 'quantity': 3, 'price': '7280'},
    {'brand': 'Zebra', 'description': 'RFD40充电底座', 'unit': '台', 'quantity': 1, 'price': '1580'},
    {'brand': 'Quad Lock', 'description': '平面支架&通用3M背贴卡扣', 'unit': '个', 'quantity': 1, 'price': '550'},
    {'brand': 'Belkin', 'description': '贝尔金 Lightning闪电耳机转接头 二合一 Type+3.5mm', 'unit': '个', 'quantity': 1, 'price': '238'},
    {'brand': 'Nexans', 'description': '2米六类非屏蔽跳线', 'unit': '条', 'quantity': 35, 'price': '25'},
    {'brand': '公牛', 'description': '5位总控3米插线板 GN-602', 'unit': '个', 'quantity': 3, 'price': '49'},
]

BOU_ITEMS = [
    {'brand': '', 'description': 'T14风扇更换 - 1S21AJS7TJ01PF46YRLP', 'unit': '项', 'quantity': 1,
     'price': '600', 'tax_rate': '6'},
]

HEADERS = ['序列号', '产品品牌', '产品描述', '采购方品牌', '采购方用户', '单位',
           '不含税单价', '含税单价', '数量', '不含税总价', '含税总价']


def _quotation_xlsx_bytes(items, *, customer_name='巴黎世家贸易（上海）有限公司', customer_code='BAL',
                          attn='Sophie Gao', tel='', quote_date=date(2026, 10, 16),
                          valid_until=date(2026, 11, 15), tax_rate=Decimal('13'),
                          remarks='1. 此报价为测试采购项目\n\n2. 全部产品保修遵循相关官方政策',
                          header_row=12, sheet_name='Sheet1', totals_override=None,
                          with_scratch_sheets=True):
    """Reproduce the real quotation template, float noise included."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name

    sheet['I2'] = 'Date:'
    sheet['J2'] = datetime.combine(quote_date, time())
    sheet['I3'] = ' Quote Date:'
    sheet['J3'] = datetime.combine(quote_date, time())
    sheet['I4'] = 'Validate Date:'
    sheet['J4'] = datetime.combine(valid_until, time())

    sheet['A7'] = 'Quotation'
    sheet.merge_cells('A7:K7')
    sheet['A8'] = 'To:'
    sheet['B8'] = customer_name
    sheet.merge_cells('B8:C8')
    sheet['G8'] = 'From:'
    sheet['I8'] = 'Leo Yao'
    sheet['A9'] = 'Attn:'
    sheet['B9'] = attn
    sheet.merge_cells('B9:C9')
    sheet['G9'] = 'MP: '
    sheet['I9'] = '13764570476'
    sheet['A10'] = 'Tel:'
    sheet['B10'] = tel or None
    sheet.merge_cells('B10:C10')
    sheet['G10'] = 'E-Mail:  '
    sheet['I10'] = 'Leo.Yao@hengji-info.com'

    for column, text in enumerate(HEADERS, start=1):
        sheet.cell(row=header_row, column=column, value=text)

    total_ex = Decimal('0')
    total_tax = Decimal('0')
    row_index = header_row + 1
    for sequence, item in enumerate(items, start=1):
        rate = Decimal(str(item.get('tax_rate', tax_rate)))
        price = Decimal(str(item['price']))
        quantity = item['quantity']
        line_ex = price * quantity
        line_tax = (line_ex * rate / Decimal('100')).quantize(Decimal('0.01'))
        total_ex += line_ex
        total_tax += line_tax
        values = [
            sequence,
            item.get('brand') or None,
            item['description'],
            item.get('user_brand', customer_code),
            item.get('user_name', 'SH P66 relocation'),
            item['unit'],
            float(price),
            float(price * (Decimal('1') + rate / Decimal('100'))),
            quantity,
            float(line_ex),
            float(line_ex + line_tax),
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_index, column=column, value=value)
        row_index += 1

    override = totals_override or {}
    total_incl = total_ex + total_tax
    sheet.cell(row=row_index, column=1, value='不含税总价 Total Price without VAT:')
    sheet.cell(row=row_index, column=11, value=override.get('without_tax', float(total_ex)))
    sheet.cell(row=row_index + 1, column=1, value=f'税金 VAT({tax_rate}%)')
    sheet.cell(row=row_index + 1, column=11, value=override.get('tax', float(total_tax)))
    sheet.cell(row=row_index + 2, column=1, value=f'含税({tax_rate}%)总价 Total Price Including VAT({tax_rate}%):')
    sheet.cell(row=row_index + 2, column=11, value=override.get('with_tax', float(total_incl)))
    row_index += 3

    sheet.cell(row=row_index, column=1, value='Remark:')
    sheet.cell(row=row_index, column=2, value=remarks)
    sheet.merge_cells(start_row=row_index, start_column=2, end_row=row_index, end_column=11)
    row_index += 3

    sheet.cell(row=row_index, column=1, value='Confirmed By :')
    sheet.cell(row=row_index, column=7, value='Prepared By :')
    sheet.cell(row=row_index + 1, column=1, value=customer_name)
    sheet.cell(row=row_index + 1, column=7, value='上海珩际信息科技有限公司')
    sheet.cell(row=row_index + 4, column=1, value='Signature & Co. Chop')
    sheet.cell(row=row_index + 4, column=7, value='Signature & Co. Chop')
    sheet.cell(row=row_index + 5, column=1, value='Date:')
    sheet.cell(row=row_index + 5, column=7, value='Date:')

    if with_scratch_sheets:
        scratch = workbook.create_sheet('Sheet2')
        scratch['B1'] = 'port'
        scratch['C1'] = 'amount'
        scratch['D1'] = 'total'
        scratch['E1'] = 'html'
        scratch['A2'] = '工位'
        scratch['B2'] = 2
        scratch['C2'] = 32
        scratch['D2'] = 64
        workbook.create_sheet('Sheet3')

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(name, content):
    return SimpleUploadedFile(name, content, content_type=XLSX_TYPE)


def _bal_file(name='HJ-BAL-SH P66 relocation-新店设备.xlsx', **kwargs):
    return _upload(name, _quotation_xlsx_bytes(BAL_ITEMS, **kwargs))


def _bou_file(name='HJ-BOU- Lynn LIN - T14风扇更换.xlsx'):
    return _upload(name, _quotation_xlsx_bytes(
        BOU_ITEMS, customer_name='开云（上海）钟表珠宝有限公司', customer_code='BOU',
        attn='Iceman Hu', quote_date=date(2025, 10, 12), valid_until=date(2025, 11, 11),
        tax_rate=Decimal('6'), remarks='1. 此报价为BOU  Lynn LIN – T14风扇上门更换费用。',
    ))


def _decisions(payload, *, row_actions=None, customer_overrides=None, status=None, excluded=()):
    """Build the mappings JSON the preview page's submit handler would produce."""
    files = {}
    for entry in payload['files']:
        file_id = entry['file_id']
        if file_id in excluded:
            files[file_id] = {'excluded': True}
            continue
        rows = {}
        for row in entry['rows']:
            override = (row_actions or {}).get(row['row_id'])
            if override is not None:
                rows[row['row_id']] = override
                continue
            suggested = row['match']['suggested_id']
            rows[row['row_id']] = (
                {'action': 'map', 'product_price_id': suggested} if suggested else {'action': ''}
            )
        files[file_id] = {
            'customer_id': (customer_overrides or {}).get(file_id, entry['customer_id']),
            'status': status or entry['status'],
            'excluded': False,
            'rows': rows,
        }
    return {'files': files}


def _ensure_roles():
    for code, (name, description) in ROLE_DEFAULTS.items():
        AdminRole.objects.get_or_create(
            code=code, defaults={'name': name, 'description': description, 'is_active': True}
        )


def _make_user(username, role_code):
    user = User.objects.create_user(username=username, password='pw')
    user.set_admin_roles([role_code])
    return user


class ImportFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        _ensure_roles()
        cls.specialist = _make_user('spec', 'order_management_specialist')
        cls.viewer = _make_user('viewer', 'viewer')
        cls.bal = Company.objects.create(name='巴黎世家贸易（上海）有限公司', code='BAL')
        cls.bou = Company.objects.create(name='开云（上海）钟表珠宝有限公司', code='BOU')
        cls.bv = Company.objects.create(name='柏蒂温妮达（中国）贸易有限公司', code='BV')
        cls.gg = Company.objects.create(name='古驰（中国）贸易有限公司', code='GG')

        cls.dell_brand = AssetBrand.objects.create(name='Dell', code='DELL')
        cls.dell_model = AssetModel.objects.create(
            brand=cls.dell_brand, name='E1715S 17寸显示器', model_number='E1715S', unit='台'
        )
        cls.dell_price = ProductPrice.objects.create(
            model=cls.dell_model, price_without_tax=Decimal('750'), tax_rate=Decimal('13')
        )
        cls.zebra_brand = AssetBrand.objects.create(name='Zebra', code='ZEBRA')
        cls.zebra_model = AssetModel.objects.create(
            brand=cls.zebra_brand, name='DS2208', model_number='DS2208', unit='个'
        )
        cls.zebra_price = ProductPrice.objects.create(
            model=cls.zebra_model, price_without_tax=Decimal('448'), tax_rate=Decimal('13')
        )
        cls.service = ServiceItem.objects.create(name='T14风扇更换', unit='项')
        cls.service_price = ProductPrice.objects.create(
            service_item=cls.service, price_without_tax=Decimal('600'), tax_rate=Decimal('6')
        )


class ParserTests(ImportFixtureMixin, TestCase):
    def test_parses_real_layout(self):
        parsed = parse_quotation_workbook(_bal_file())
        self.assertEqual(parsed['sheet_name'], 'Sheet1')
        self.assertEqual(parsed['header_row'], 12)
        self.assertEqual(len(parsed['rows']), 3)
        self.assertEqual(parsed['customer_name'], '巴黎世家贸易（上海）有限公司')
        self.assertEqual(parsed['attn'], 'Sophie Gao')
        self.assertEqual(parsed['tel'], '')
        self.assertEqual(parsed['codes'], ['BAL'])
        self.assertEqual(parsed['quotation_date'], '2026-10-16')
        self.assertEqual(parsed['valid_until'], '2026-11-15')
        self.assertEqual(parsed['totals'], {
            'without_tax': '1716.00', 'tax': '223.08', 'with_tax': '1939.08',
        })
        self.assertEqual(parsed['label_rate'], '13')
        self.assertIn('\n\n', parsed['remarks'])
        self.assertEqual(parsed['errors'], [])
        self.assertEqual(parsed['rows'][1]['brand'], '')
        self.assertEqual(parsed['rows'][1]['description'], '显示器支架')

    def test_full_eighteen_line_totals_match_the_source(self):
        content = _quotation_xlsx_bytes(FULL_BAL_ITEMS)
        parsed = parse_quotation_workbook(_upload('full.xlsx', content))
        self.assertEqual(len(parsed['rows']), 18)
        self.assertEqual(parsed['totals']['without_tax'], '52251.00')
        self.assertEqual(parsed['totals']['tax'], '6792.63')
        self.assertEqual(parsed['totals']['with_tax'], '59043.63')
        self.assertEqual(parsed['rows'][6]['brand'], 'HP')
        self.assertEqual(parsed['rows'][16]['quantity'], 35)

    def test_supplier_contact_block_is_never_customer_data(self):
        parsed = parse_quotation_workbook(_bal_file())
        self.assertNotIn('attn_email', parsed)
        self.assertNotIn('Leo.Yao@hengji-info.com', json.dumps(parsed, ensure_ascii=False))
        self.assertNotIn('Leo Yao', json.dumps(parsed, ensure_ascii=False))
        self.assertEqual(parsed['tel'], '')

    def test_service_file_derives_six_percent(self):
        parsed = parse_quotation_workbook(_bou_file())
        self.assertEqual(len(parsed['rows']), 1)
        self.assertEqual(parsed['label_rate'], '6')
        self.assertEqual(parsed['totals'], {
            'without_tax': '600.00', 'tax': '36.00', 'with_tax': '636.00',
        })
        payload = build_preview_payload([_bou_file()], 'draft')
        self.assertEqual(payload['files'][0]['rows'][0]['tax_rate'], '6.00')

    def test_float_noise_is_quantized(self):
        parsed = parse_quotation_workbook(_bal_file())
        self.assertEqual(parsed['rows'][0]['price_with_tax'], '847.50')
        self.assertEqual(parsed['rows'][0]['unit_price'], '750.00')
        noisy = parse_quotation_workbook(_upload('gg.xlsx', _quotation_xlsx_bytes(
            BAL_ITEMS[:1], totals_override={'tax': 106.60000000000001},
        )))
        self.assertEqual(noisy['totals']['tax'], '106.60')

    def test_header_drift_and_renamed_sheet(self):
        content = _quotation_xlsx_bytes(BAL_ITEMS, header_row=15, sheet_name='Quote')
        parsed = parse_quotation_workbook(_upload('drift.xlsx', content))
        self.assertEqual(parsed['sheet_name'], 'Quote')
        self.assertEqual(parsed['header_row'], 15)
        self.assertEqual(len(parsed['rows']), 3)
        self.assertEqual(parsed['quotation_date'], '2026-10-16')

    def test_signature_date_does_not_override_quote_date(self):
        parsed = parse_quotation_workbook(_bal_file())
        self.assertEqual(parsed['quotation_date'], '2026-10-16')
        self.assertEqual(parsed['valid_until'], '2026-11-15')

    def test_missing_header_raises(self):
        workbook = Workbook()
        workbook.active['A1'] = 'nothing useful'
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaises(ValueError):
            parse_quotation_workbook(_upload('bad.xlsx', buffer.getvalue()))

    def test_missing_required_column_raises(self):
        content = _quotation_xlsx_bytes(BAL_ITEMS)
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(content))
        workbook['Sheet1'].cell(row=12, column=9, value='备注')
        buffer = io.BytesIO()
        workbook.save(buffer)
        with self.assertRaises(ValueError) as caught:
            parse_quotation_workbook(_upload('missing.xlsx', buffer.getvalue()))
        self.assertIn('quantity', str(caught.exception))

    def test_invalid_quantity_is_a_file_error(self):
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(_quotation_xlsx_bytes(BAL_ITEMS)))
        workbook['Sheet1'].cell(row=13, column=9, value='abc')
        buffer = io.BytesIO()
        workbook.save(buffer)
        parsed = parse_quotation_workbook(_upload('qty.xlsx', buffer.getvalue()))
        self.assertTrue(parsed['errors'])
        self.assertEqual(len(parsed['rows']), 2)

    def test_garbage_bytes_raise(self):
        with self.assertRaises(ValueError):
            parse_quotation_workbook(_upload('garbage.xlsx', b'not a zip at all'))

    def test_scratch_sheet_is_ignored(self):
        parsed = parse_quotation_workbook(_bal_file())
        self.assertEqual(parsed['sheet_name'], 'Sheet1')
        self.assertEqual(len(parsed['rows']), 3)


class MatcherTests(ImportFixtureMixin, TestCase):
    def _row(self, description, brand=''):
        from quotations.import_xlsx import _model_tokens
        return {'description': description, 'brand': brand, 'model_tokens': _model_tokens(description)}

    def test_model_number_single_hit_is_suggested(self):
        result = match_row_candidates(self._row('E1715S 17寸显示器', 'Dell'), catalog_prices())
        self.assertEqual(result['confidence'], 'model_number')
        self.assertEqual(result['suggested_id'], self.dell_price.pk)

    def test_ambiguous_model_number_has_no_suggestion(self):
        other_brand = AssetBrand.objects.create(name='Other', code='OTHER')
        other_model = AssetModel.objects.create(
            brand=other_brand, name='E1715S clone', model_number='E1715S', unit='台'
        )
        ProductPrice.objects.create(model=other_model, price_without_tax=Decimal('700'))
        result = match_row_candidates(self._row('E1715S 17寸显示器'), catalog_prices())
        self.assertEqual(len(result['candidates']), 2)
        self.assertIsNone(result['suggested_id'])

    def test_brandless_service_row_matches_by_text(self):
        result = match_row_candidates(
            self._row('T14风扇更换 - 1S21AJS7TJ01PF46YRLP'), catalog_prices()
        )
        self.assertEqual(result['suggested_id'], self.service_price.pk)

    def test_unmatched_row(self):
        result = match_row_candidates(self._row('显示器支架'), catalog_prices())
        self.assertEqual(result['confidence'], 'none')
        self.assertEqual(result['candidates'], [])
        self.assertIsNone(result['suggested_id'])

    def test_matching_never_calls_the_llm(self):
        build_preview_payload([_bal_file(), _bou_file()], 'draft')
        self.assertEqual(LlmCallRecord.objects.count(), 0)


class ImportViewTests(ImportFixtureMixin, TestCase):
    def setUp(self):
        self.client.force_login(self.specialist)
        self.url = reverse('quotations:import')

    def _upload_files(self, files, status='draft'):
        response = self.client.post(self.url, {'status': status, 'files': files})
        self.assertEqual(response.status_code, 200)
        return response, self.client.session[IMPORT_PREVIEW_SESSION_KEY]

    def _confirm(self, decisions):
        return self.client.post(self.url, {
            'confirm_import': '1',
            'mappings_payload': json.dumps(decisions),
        })

    def _skip_unmatched(self, payload):
        actions = {
            row['row_id']: {'action': 'skip'}
            for entry in payload['files'] for row in entry['rows']
            if not row['match']['suggested_id']
        }
        return _decisions(payload, row_actions=actions)

    def test_list_page_has_the_import_button(self):
        response = self.client.get(reverse('quotations:list'))
        self.assertContains(response, self.url)
        self.assertContains(response, 'Import from Excel')

    def test_upload_builds_preview(self):
        response, payload = self._upload_files([_bal_file()])
        self.assertTrue(response.context['preview_mode'])
        self.assertEqual(len(payload['files']), 1)
        entry = payload['files'][0]
        self.assertEqual(entry['customer_id'], self.bal.pk)
        self.assertEqual(entry['customer_match'], 'code')
        self.assertEqual(entry['status'], 'draft')
        self.assertTrue(entry['reconciliation_ok'])
        self.assertEqual(entry['rows'][0]['match']['suggested_id'], self.dell_price.pk)
        self.assertIsNone(entry['rows'][1]['match']['suggested_id'])

    def test_batch_dedups_byte_identical_files(self):
        original = _bal_file()
        copy = _upload('HJ-BAL-SH P66 relocation-新店设备(1).xlsx', original.read())
        original.seek(0)
        response, payload = self._upload_files([original, copy, _bou_file()])
        self.assertEqual(len(payload['files']), 2)
        self.assertEqual(len(payload['duplicates']), 1)
        self.assertEqual(payload['duplicates'][0]['duplicate_of'], original.name)
        self.assertContains(response, 'Duplicate of')

        confirm = self._confirm(self._skip_unmatched(payload))
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(Quotation.objects.count(), 2)

    def test_confirm_happy_path(self):
        _, payload = self._upload_files([_bal_file()])
        response = self._confirm(self._skip_unmatched(payload))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'common/import_result.html')

        quotation = Quotation.objects.get()
        self.assertTrue(quotation.quotation_number.startswith('QT-'))
        self.assertEqual(quotation.customer, self.bal)
        self.assertEqual(quotation.status, 'draft')
        self.assertFalse(quotation.requires_confirmation)
        self.assertEqual(quotation.quotation_date, date(2026, 10, 16))
        self.assertEqual(quotation.valid_until, date(2026, 11, 15))
        self.assertEqual(quotation.attn, 'Sophie Gao')
        self.assertEqual(quotation.tel, '')
        self.assertEqual(quotation.attn_email, '')
        self.assertIn('\n\n', quotation.remarks)
        self.assertIn('HJ-BAL-SH P66 relocation-新店设备.xlsx', quotation.notes)
        self.assertIn('HJ-BAL-SH P66 relocation-新店设备', quotation.notes)

        items = list(quotation.items.all())
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].product_description, 'E1715S 17寸显示器')
        self.assertEqual(items[0].unit_price, Decimal('750.00'))
        self.assertEqual(items[0].tax_rate, Decimal('13.00'))
        self.assertEqual(items[0].unit, '台')
        self.assertEqual(items[0].user_brand, 'BAL')
        self.assertEqual(items[0].user_name, 'SH P66 relocation')
        self.assertEqual(items[1].product_description, 'DS2208')
        self.assertEqual(items[1].quantity, 2)
        self.assertEqual(quotation.total_without_tax, Decimal('1646.00'))
        self.assertEqual(quotation.total_tax, Decimal('213.98'))
        self.assertEqual(quotation.total_with_tax, Decimal('1859.98'))
        self.assertNotIn(IMPORT_PREVIEW_SESSION_KEY, self.client.session)

    def test_chosen_status_is_honored(self):
        _, payload = self._upload_files([_bal_file()], status='sent')
        self._confirm(self._skip_unmatched(payload))
        self.assertEqual(Quotation.objects.get().status, 'sent')

    def test_excel_price_wins_over_catalog_snapshot(self):
        self.dell_price.price_without_tax = Decimal('999')
        self.dell_price.save()
        _, payload = self._upload_files([_bal_file()])
        self._confirm(self._skip_unmatched(payload))
        item = QuotationItem.objects.get(product_description='E1715S 17寸显示器')
        self.assertEqual(item.unit_price, Decimal('750.00'))
        self.assertEqual(item.tax_rate, Decimal('13.00'))
        self.assertEqual(item.unit, '台')
        self.assertEqual(item.brand_name, 'Dell')

    def test_brandless_row_keeps_excel_description(self):
        _, payload = self._upload_files([_bal_file()])
        actions = {payload['files'][0]['rows'][1]['row_id']: {
            'action': 'map', 'product_price_id': self.dell_price.pk,
        }}
        self._confirm(_decisions(payload, row_actions=actions))
        item = QuotationItem.objects.get(product_description='显示器支架')
        self.assertEqual(item.brand_name, 'Dell')
        self.assertEqual(item.unit_price, Decimal('70.00'))
        self.assertEqual(item.unit, '个')

    def test_mixed_tax_rates_are_preserved_per_line(self):
        items = BAL_ITEMS[:1] + BOU_ITEMS
        content = _quotation_xlsx_bytes(items, tax_rate=Decimal('13'))
        _, payload = self._upload_files([_upload('mixed.xlsx', content)])
        entry = payload['files'][0]
        self.assertEqual(entry['rows'][0]['tax_rate'], '13.00')
        self.assertEqual(entry['rows'][1]['tax_rate'], '6.00')
        self._confirm(_decisions(payload))
        quotation = Quotation.objects.get()
        self.assertEqual(quotation.total_without_tax, Decimal('1350.00'))
        self.assertEqual(quotation.total_tax, Decimal('133.50'))
        self.assertEqual(quotation.total_with_tax, Decimal('1483.50'))

    def test_unmapped_line_blocks_confirm(self):
        _, payload = self._upload_files([_bal_file()])
        response = self._confirm(_decisions(payload))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['confirm_errors'])
        self.assertEqual(Quotation.objects.count(), 0)
        self.assertEqual(ImportRun.objects.count(), 0)

    def test_create_new_catalog_entry(self):
        _, payload = self._upload_files([_bal_file()])
        row = payload['files'][0]['rows'][1]
        actions = {row['row_id']: {'action': 'create', 'create': {
            'catalog_type': 'hardware', 'brand_name': '浩顺', 'model_name': '显示器支架',
            'model_number': '', 'service_name': '', 'service_group': '',
        }}}
        self._confirm(_decisions(payload, row_actions=actions))
        brand = AssetBrand.objects.get(name='浩顺')
        self.assertTrue(brand.code)
        model = AssetModel.objects.get(brand=brand, name='显示器支架')
        self.assertEqual(model.unit, '个')
        price = ProductPrice.objects.get(model=model, is_current=True)
        self.assertEqual(price.price_without_tax, Decimal('70.00'))
        item = QuotationItem.objects.get(product_description='显示器支架')
        self.assertEqual(item.product_price, price)
        self.assertEqual(item.unit_price, Decimal('70.00'))

    def test_create_new_service_entry(self):
        _, payload = self._upload_files([_bou_file()])
        row = payload['files'][0]['rows'][0]
        actions = {row['row_id']: {'action': 'create', 'create': {
            'catalog_type': 'service', 'service_name': '风扇更换服务', 'service_group': '维修',
            'brand_name': '', 'model_name': '', 'model_number': '',
        }}}
        self._confirm(_decisions(payload, row_actions=actions))
        service = ServiceItem.objects.get(name='风扇更换服务')
        self.assertEqual(service.unit, '项')
        price = ProductPrice.objects.get(service_item=service, is_current=True)
        self.assertEqual(price.tax_rate, Decimal('6.00'))
        self.assertEqual(QuotationItem.objects.get().tax_rate, Decimal('6.00'))

    def test_two_create_rows_share_one_catalog_entry(self):
        items = [
            {'brand': '公牛', 'description': '插线板', 'unit': '个', 'quantity': 1, 'price': '90'},
            {'brand': '公牛', 'description': '插线板', 'unit': '个', 'quantity': 2, 'price': '90'},
        ]
        _, payload = self._upload_files([_upload('dup.xlsx', _quotation_xlsx_bytes(items))])
        actions = {
            row['row_id']: {'action': 'create', 'create': {
                'catalog_type': 'hardware', 'brand_name': '公牛', 'model_name': '插线板',
                'model_number': '', 'service_name': '', 'service_group': '',
            }}
            for row in payload['files'][0]['rows']
        }
        self._confirm(_decisions(payload, row_actions=actions))
        self.assertEqual(AssetModel.objects.filter(name='插线板').count(), 1)
        self.assertEqual(ProductPrice.objects.filter(model__name='插线板').count(), 1)
        self.assertEqual(QuotationItem.objects.count(), 2)

    def test_create_collision_is_blocked(self):
        _, payload = self._upload_files([_bal_file()])
        row = payload['files'][0]['rows'][0]
        actions = {row['row_id']: {'action': 'create', 'create': {
            'catalog_type': 'hardware', 'brand_name': 'Dell', 'model_name': 'E1715S 17寸显示器',
            'model_number': 'E1715S', 'service_name': '', 'service_group': '',
        }}}
        response = self._confirm(_decisions(payload, row_actions=actions))
        self.assertTrue(response.context['confirm_errors'])
        self.assertEqual(Quotation.objects.count(), 0)
        self.assertEqual(ProductPrice.objects.count(), 3)

    def test_unmatched_customer_requires_manual_choice(self):
        content = _quotation_xlsx_bytes(
            BAL_ITEMS, customer_name='不存在的客户有限公司', customer_code='ZZZ'
        )
        _, payload = self._upload_files([_upload('unknown.xlsx', content)])
        entry = payload['files'][0]
        self.assertIsNone(entry['customer_id'])
        response = self._confirm(self._skip_unmatched(payload))
        self.assertTrue(response.context['confirm_errors'])
        self.assertEqual(Quotation.objects.count(), 0)
        self.assertEqual(Company.objects.count(), 4)

        actions = self._skip_unmatched(payload)['files']
        actions[entry['file_id']]['customer_id'] = self.gg.pk
        self._confirm({'files': actions})
        self.assertEqual(Quotation.objects.get().customer, self.gg)
        self.assertEqual(Company.objects.count(), 4)

    def test_customer_matched_by_name_when_code_is_unknown(self):
        content = _quotation_xlsx_bytes(BAL_ITEMS, customer_code='UNKNOWN')
        _, payload = self._upload_files([_upload('name.xlsx', content)])
        self.assertEqual(payload['files'][0]['customer_id'], self.bal.pk)
        self.assertEqual(payload['files'][0]['customer_match'], 'name')

    def test_totals_mismatch_warns_but_does_not_block(self):
        content = _quotation_xlsx_bytes(BAL_ITEMS, totals_override={'with_tax': 9999.0})
        response, payload = self._upload_files([_upload('tampered.xlsx', content)])
        entry = payload['files'][0]
        self.assertFalse(entry['reconciliation_ok'])
        self.assertContains(response, 'Totals do not match the source file.')
        self._confirm(self._skip_unmatched(payload))
        self.assertEqual(Quotation.objects.count(), 1)

    def test_excluded_file_is_not_imported(self):
        _, payload = self._upload_files([_bal_file(), _bou_file()])
        decisions = self._skip_unmatched(payload)
        decisions['files'][payload['files'][1]['file_id']] = {'excluded': True}
        self._confirm(decisions)
        self.assertEqual(Quotation.objects.count(), 1)
        self.assertEqual(Quotation.objects.get().customer, self.bal)

    def test_rollback_removes_quotations_and_created_catalog(self):
        _, payload = self._upload_files([_bal_file()])
        row = payload['files'][0]['rows'][1]
        actions = {row['row_id']: {'action': 'create', 'create': {
            'catalog_type': 'hardware', 'brand_name': '浩顺', 'model_name': '显示器支架',
            'model_number': '', 'service_name': '', 'service_group': '',
        }}}
        self._confirm(_decisions(payload, row_actions=actions))
        run = ImportRun.objects.get()
        self.assertEqual(run.status, 'completed')
        self.assertTrue(run.can_rollback)

        response = self.client.post(reverse('quotations:import_rollback'))
        self.assertRedirects(response, self.url)
        self.assertEqual(Quotation.objects.count(), 0)
        self.assertEqual(QuotationItem.objects.count(), 0)
        self.assertEqual(AssetBrand.objects.filter(name='浩顺').count(), 0)
        self.assertEqual(AssetModel.objects.filter(name='显示器支架').count(), 0)
        self.assertEqual(ProductPrice.objects.count(), 3)
        self.assertEqual(Company.objects.count(), 4)
        run.refresh_from_db()
        self.assertEqual(run.status, 'rolled_back')
        self.assertFalse(run.can_rollback)

        again = self.client.post(reverse('quotations:import_rollback'))
        self.assertRedirects(again, self.url)
        self.assertEqual(Quotation.objects.count(), 0)

    def test_rollback_blocked_when_catalog_is_reused(self):
        _, payload = self._upload_files([_bal_file()])
        row = payload['files'][0]['rows'][1]
        actions = {row['row_id']: {'action': 'create', 'create': {
            'catalog_type': 'hardware', 'brand_name': '浩顺', 'model_name': '显示器支架',
            'model_number': '', 'service_name': '', 'service_group': '',
        }}}
        self._confirm(_decisions(payload, row_actions=actions))
        created_price = ProductPrice.objects.get(model__name='显示器支架')
        other = Quotation.objects.create(
            customer=self.bal, quotation_date=date(2026, 1, 1), valid_until=date(2026, 2, 1),
        )
        QuotationItem.objects.create(
            quotation=other, product_price=created_price, brand_name='浩顺',
            product_description='显示器支架', unit='个', quantity=1,
            unit_price=Decimal('70'), tax_rate=Decimal('13'),
        )
        response = self.client.post(reverse('quotations:import_rollback'))
        self.assertRedirects(response, self.url)
        self.assertEqual(ImportRun.objects.get().status, 'completed')
        self.assertTrue(ProductPrice.objects.filter(pk=created_price.pk).exists())

    def test_rejects_legacy_xls(self):
        response = self.client.post(self.url, {
            'status': 'draft', 'files': [_upload('old.xls', b'whatever')],
        })
        self.assertTrue(response.context['upload_errors'])
        self.assertNotIn(IMPORT_PREVIEW_SESSION_KEY, self.client.session)

    def test_rejects_oversize_file(self):
        response = self.client.post(self.url, {
            'status': 'draft', 'files': [_upload('big.xlsx', b'x' * (MAX_FILE_BYTES + 1))],
        })
        self.assertTrue(response.context['upload_errors'])

    def test_rejects_oversize_batch(self):
        chunk = b'x' * (8 * 1024 * 1024)
        response = self.client.post(self.url, {
            'status': 'draft',
            'files': [_upload('a.xlsx', chunk), _upload('b.xlsx', chunk)],
        })
        self.assertTrue(response.context['upload_errors'])

    def test_rejects_too_many_files(self):
        response = self.client.post(self.url, {
            'status': 'draft',
            'files': [_upload(f'f{i}.xlsx', b'x') for i in range(11)],
        })
        self.assertTrue(response.context['upload_errors'])

    def test_rejects_empty_file(self):
        response = self.client.post(self.url, {
            'status': 'draft', 'files': [_upload('empty.xlsx', b'')],
        })
        self.assertTrue(response.context['upload_errors'])

    def test_confirm_without_preview_redirects(self):
        response = self._confirm({'files': {}})
        self.assertRedirects(response, self.url)
        self.assertEqual(Quotation.objects.count(), 0)

    def test_confirm_post_carries_only_three_fields(self):
        items = [BAL_ITEMS[0]] * 200
        _, payload = self._upload_files([_upload('many.xlsx', _quotation_xlsx_bytes(items))])
        self.assertEqual(len(payload['files'][0]['rows']), 200)
        response = self._confirm(_decisions(payload))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.wsgi_request.POST.keys()), {'confirm_import', 'mappings_payload'}
        )
        self.assertEqual(Quotation.objects.get().items.count(), 200)

    def test_permission_gate(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('dashboard:dashboard'))
        response = self.client.post(self.url, {'status': 'draft', 'files': [_bal_file()]})
        self.assertRedirects(response, reverse('dashboard:dashboard'))
        response = self.client.post(reverse('quotations:import_rollback'))
        self.assertRedirects(response, reverse('dashboard:dashboard'))
        self.assertEqual(Quotation.objects.count(), 0)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_chinese_translation_is_applied(self):
        # CI does not compile the gitignored .mo catalogs, so build one into a
        # temporary locale tree instead of relying on a local compile step.
        import polib
        from django.conf import settings
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as tmp:
            messages_dir = os.path.join(tmp, 'zh_CN', 'LC_MESSAGES')
            os.makedirs(messages_dir)
            catalog = polib.pofile(os.path.join(
                settings.BASE_DIR, 'locale', 'zh_CN', 'LC_MESSAGES', 'django.po'
            ))
            catalog.save_as_mofile(os.path.join(messages_dir, 'django.mo'))
            with override_settings(LOCALE_PATHS=[tmp]):
                with translation.override('zh-cn'):
                    response = self.client.get(reverse('quotations:list'))
                    self.assertContains(response, '从Excel导入')