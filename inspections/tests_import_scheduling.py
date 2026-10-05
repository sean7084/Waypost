"""Tests for schedule auto-arrangement (R1) and importer Location population.

Covers ``inspections/services/schedule_arranger.py`` (city clustering, same-address
consecutive AM/PM slots, capacity overflow) and the ``kering_import`` integration
that fills ``companies.Location`` contact/geography fields and assigns slots when
the schedule has no ``inspection_date``.
"""
import io
from datetime import date

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminRole
from companies.models import Company, Location
from inspections.models import StoreInspection
from inspections.services import kering_import, sample_files
from inspections.services.schedule_arranger import (
    ScheduleCapacityError,
    arrange,
    assign_slots_by_date,
    parse_slot,
)

User = get_user_model()


def _xlsx_bytes(rows):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name='Sheet1')
    buffer.seek(0)
    return buffer


class ScheduleArrangerTests(TestCase):
    def test_same_address_gets_consecutive_slots(self):
        rows = [
            {'city': 'Beijing', 'address': 'Beijing SKP', 'store': 'BJSK'},
            {'city': 'Beijing', 'address': 'Beijing SKP', 'store': 'BJSC'},
            {'city': 'Beijing', 'address': 'Beijing SKP', 'store': 'BJKD'},
        ]
        out = arrange(rows, date(2026, 10, 1), date(2026, 10, 3))
        # Three co-located sites occupy consecutive slots: AM d1, PM d1, AM d2.
        self.assertEqual(
            [(r['inspection_date'], r['slot']) for r in out],
            [(date(2026, 10, 1), 'am'), (date(2026, 10, 1), 'pm'), (date(2026, 10, 2), 'am')],
        )

    def test_cities_are_spread_across_range(self):
        rows = [
            {'city': 'Beijing', 'address': 'A1', 'store': 'S1'},
            {'city': 'Beijing', 'address': 'A1', 'store': 'S2'},
            {'city': 'Shanghai', 'address': 'B1', 'store': 'S3'},
            {'city': 'Shanghai', 'address': 'B1', 'store': 'S4'},
        ]
        out = arrange(rows, date(2026, 10, 1), date(2026, 10, 2))
        by_store = {r['store']: (r['inspection_date'], r['slot']) for r in out}
        # Beijing takes the first contiguous block, Shanghai the second.
        self.assertEqual(by_store['S1'], (date(2026, 10, 1), 'am'))
        self.assertEqual(by_store['S2'], (date(2026, 10, 1), 'pm'))
        self.assertEqual(by_store['S3'], (date(2026, 10, 2), 'am'))
        self.assertEqual(by_store['S4'], (date(2026, 10, 2), 'pm'))

    def test_capacity_overflow_raises(self):
        rows = [{'city': 'C', 'address': f'A{i}', 'store': f'S{i}'} for i in range(5)]
        with self.assertRaises(ScheduleCapacityError):
            arrange(rows, date(2026, 10, 1), date(2026, 10, 2))  # only 4 slots

    def test_empty_rows_returns_empty(self):
        self.assertEqual(arrange([], date(2026, 10, 1), date(2026, 10, 2)), [])

    def test_busier_store_of_a_pair_gets_the_morning(self):
        rows = [
            {'city': 'Beijing', 'address': 'SKP', 'store': 'SMALL', 'device_count': 12},
            {'city': 'Beijing', 'address': 'SKP', 'store': 'BIG', 'device_count': 71},
        ]
        out = arrange(rows, date(2026, 10, 1), date(2026, 10, 2))
        by_store = {row['store']: (row['inspection_date'], row['slot']) for row in out}
        self.assertEqual(by_store['BIG'], (date(2026, 10, 1), 'am'))
        self.assertEqual(by_store['SMALL'], (date(2026, 10, 1), 'pm'))


class SlotBalancingTests(TestCase):
    """``assign_slots_by_date``: the date is appointed, the slot is not."""

    def _rows(self, counts, day=date(2026, 10, 5), slot=None):
        return [
            {'inspection_date': day, 'device_count': count, 'slot': slot, 'store': f'S{index}'}
            for index, count in enumerate(counts)
        ]

    def test_four_sites_split_evenly_with_the_busiest_in_the_morning(self):
        rows = self._rows([10, 40, 20, 30])
        slots = assign_slots_by_date(rows)
        am = sorted(rows[i]['device_count'] for i, s in enumerate(slots) if s == 'am')
        pm = sorted(rows[i]['device_count'] for i, s in enumerate(slots) if s == 'pm')
        self.assertEqual(am, [30, 40])
        self.assertEqual(pm, [10, 20])

    def test_odd_count_gives_the_extra_site_to_the_morning(self):
        # Sorted desc 50, 25, 5 -> AM takes two, PM one.
        self.assertEqual(assign_slots_by_date(self._rows([5, 50, 25])), ['pm', 'am', 'am'])

    def test_each_date_is_balanced_independently(self):
        rows = [
            {'inspection_date': date(2026, 10, 5), 'device_count': 10},
            {'inspection_date': date(2026, 10, 5), 'device_count': 20},
            {'inspection_date': date(2026, 10, 6), 'device_count': 5},
        ]
        self.assertEqual(assign_slots_by_date(rows), ['pm', 'am', 'am'])

    def test_explicit_slot_wins_and_is_not_rebalanced(self):
        rows = [
            {'inspection_date': date(2026, 10, 5), 'device_count': 1, 'slot': 'PM'},
            {'inspection_date': date(2026, 10, 5), 'device_count': 99, 'slot': '下午'},
            {'inspection_date': date(2026, 10, 5), 'device_count': 50},
        ]
        self.assertEqual(assign_slots_by_date(rows), ['pm', 'pm', 'am'])

    def test_pinned_slot_takes_capacity_so_the_blank_neighbour_goes_pm(self):
        rows = [
            {'inspection_date': date(2026, 10, 5), 'device_count': 5, 'slot': 'AM'},
            {'inspection_date': date(2026, 10, 5), 'device_count': 50},
        ]
        self.assertEqual(assign_slots_by_date(rows), ['am', 'pm'])

    def test_more_sites_than_slots_share_the_slots(self):
        slots = assign_slots_by_date(self._rows([1, 2, 3, 4, 5]))
        self.assertEqual(slots.count('am'), 3)  # the three busiest
        self.assertEqual(slots.count('pm'), 2)

    def test_row_without_a_date_falls_back_to_the_first_slot(self):
        self.assertEqual(assign_slots_by_date([{'device_count': 3}]), ['am'])

    def test_device_count_tolerates_text_and_blanks(self):
        rows = [
            {'inspection_date': date(2026, 10, 5), 'device_count': '40'},
            {'inspection_date': date(2026, 10, 5), 'device_count': ''},
            {'inspection_date': date(2026, 10, 5), 'device_count': 7.0},
        ]
        self.assertEqual(assign_slots_by_date(rows), ['am', 'pm', 'am'])

    def test_parse_slot_aliases(self):
        cases = [('AM', 'am'), ('p.m.', 'pm'), ('上午', 'am'), ('Afternoon', 'pm'),
                 ('', None), (None, None), ('nonsense', None)]
        for value, expected in cases:
            self.assertEqual(parse_slot(value), expected, value)


class ImporterLocationAndAutoArrangeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Kering', code='KER')

    def _schedule_without_dates(self):
        return _xlsx_bytes([
            {'Brand': 'Gucci', 'Store Name': 'BJSK', 'JDA code': 22032,
             'Address': 'No.87 Jian Guo Road, Beijing SKP', 'City': 'Beijing',
             'Store Dir. Phone': '+8610 65981606', 'device_count': 71},
            {'Brand': 'Gucci', 'Store Name': 'BJSC', 'JDA code': 22152,
             'Address': 'No.87 Jian Guo Road, Beijing SKP', 'City': 'Beijing',
             'Store Dir. Phone': '+8610 65000957', 'device_count': 18},
            {'Brand': 'Gucci', 'Store Name': 'SZOL', 'JDA code': 22149,
             'Address': 'Shanghai Qiantan Ave 1', 'City': 'Shanghai',
             'Store Dir. Phone': '+8621 11112222', 'device_count': 30},
        ])

    def test_location_fields_populated_and_auto_arrange_assigns_slots(self):
        kering_import.import_kering_master(
            self._schedule_without_dates(),
            None,
            company_name='Kering',
            company_code='KER',
            batch=None,
            auto_arrange=True,
            arrange_start=date(2026, 11, 2),
            arrange_end=date(2026, 11, 4),
        )
        # Locations carry address/city/phone from the schedule.
        skp = Location.objects.get(code='22032')
        self.assertEqual(skp.city, 'Beijing')
        self.assertEqual(skp.address_line1, 'No.87 Jian Guo Road, Beijing SKP')
        self.assertEqual(skp.phone_number, '+8610 65981606')

        # Same-address Beijing sites are consecutive (AM/PM same date); Shanghai after.
        inspections = {i.jda_code: i for i in StoreInspection.objects.all()}
        self.assertEqual(inspections['22032'].inspection_date, date(2026, 11, 2))
        self.assertEqual(inspections['22032'].slot, 'am')
        self.assertEqual(inspections['22152'].inspection_date, date(2026, 11, 2))
        self.assertEqual(inspections['22152'].slot, 'pm')
        self.assertEqual(inspections['22149'].inspection_date, date(2026, 11, 3))
        self.assertEqual(inspections['22149'].slot, 'am')

    def test_missing_dates_without_auto_arrange_are_skipped(self):
        kering_import.import_kering_master(
            self._schedule_without_dates(),
            None,
            company_name='Kering',
            company_code='KER',
            batch=None,
            auto_arrange=False,
        )
        # No inspections created (rows lacked dates), but locations still imported.
        self.assertEqual(StoreInspection.objects.count(), 0)
        self.assertEqual(Location.objects.count(), 3)


class ImporterSlotBalancingTests(TestCase):
    """End-to-end: schedules that appoint a date but not an AM/PM slot."""

    def setUp(self):
        Company.objects.create(name='Kering', code='KER')

    def _schedule(self, counts, slot_value=None):
        rows = []
        for jda, count in counts:
            row = {
                'Brand': 'Gucci', 'Store Name': f'S{jda}', 'JDA code': jda,
                'Address': 'Same Mall', 'City': 'Beijing',
                'inspection_date': '2026-10-05', 'device_count': count,
            }
            if slot_value is not None:
                row['AM/PM'] = slot_value
            rows.append(row)
        return _xlsx_bytes(rows)

    def _assets(self, per_jda):
        rows = []
        for jda, count in per_jda.items():
            for index in range(count):
                rows.append({'STORE': f'S{jda}', 'JDA': jda, 'Category': 'Desktop',
                             'Brand-Model': 'Lenovo-M70Q', 'SN': f'{jda}-SN{index}',
                             'Asset ID': f'{jda}-A{index}'})
        return _xlsx_bytes(rows)

    def test_dated_rows_are_balanced_busiest_first(self):
        kering_import.import_kering_master(
            self._schedule([(22001, 10), (22002, 40), (22003, 20), (22004, 30)]),
            None, company_name='Kering', company_code='KER', batch=None,
        )
        slots = {i.jda_code: i.slot for i in StoreInspection.objects.all()}
        self.assertEqual(slots, {'22002': 'am', '22004': 'am', '22003': 'pm', '22001': 'pm'})
        self.assertEqual(
            StoreInspection.objects.filter(inspection_date=date(2026, 10, 5)).count(), 4)

    def test_asset_list_counts_win_over_the_schedule_column(self):
        # The schedule claims 22001 is the big store; the asset list disagrees.
        kering_import.import_kering_master(
            self._schedule([(22001, 99), (22002, 1)]),
            self._assets({22001: 2, 22002: 8}),
            company_name='Kering', company_code='KER', batch=None,
        )
        slots = {i.jda_code: i.slot for i in StoreInspection.objects.all()}
        self.assertEqual(slots['22002'], 'am')  # 8 real devices beats a claimed 99
        self.assertEqual(slots['22001'], 'pm')

    def test_explicit_slot_column_is_honoured(self):
        kering_import.import_kering_master(
            self._schedule([(22001, 10), (22002, 40)], slot_value='PM'),
            None, company_name='Kering', company_code='KER', batch=None,
        )
        slots = {i.jda_code: i.slot for i in StoreInspection.objects.all()}
        self.assertEqual(slots, {'22001': 'pm', '22002': 'pm'})

    def test_single_site_on_a_date_takes_the_morning(self):
        kering_import.import_kering_master(
            self._schedule([(22001, 10)]),
            None, company_name='Kering', company_code='KER', batch=None,
        )
        self.assertEqual(StoreInspection.objects.get().slot, 'am')


class SampleFileTests(TestCase):
    """The downloadable samples are generated from the parser's own column maps."""

    def setUp(self):
        for code, name in [('superadmin', 'Superadmin'), ('inspection_engineer', 'Field Engineer')]:
            AdminRole.objects.get_or_create(code=code, defaults={'name': name, 'is_active': True})
        self.manager = User.objects.create_user(username='admin', password='pw')
        self.manager.set_admin_roles([User.AdminRole.SUPERADMIN])
        Company.objects.create(name='Kering', code='KER')

    def test_schedule_sample_round_trips_through_the_parser(self):
        content, filename = sample_files.schedule_sample()
        rows = kering_import._read_rows(SimpleUploadedFile(filename, content))
        self.assertEqual(len(rows), len(sample_files.SCHEDULE_SAMPLE_ROWS))
        first = rows[0]
        for field, expected in (('jda', '22032'), ('brand', 'Gucci'), ('city', 'Beijing'),
                                ('address', 'No.87 Jian Guo Road, Beijing SKP'),
                                ('device_count', '71'), ('date', ''), ('slot', '')):
            self.assertEqual(
                str(kering_import._get(first, kering_import.SCHEDULE_COLUMNS[field])).strip(),
                expected, field)

    def test_asset_sample_round_trips_through_the_parser(self):
        content, filename = sample_files.asset_list_sample()
        rows = kering_import._read_rows(SimpleUploadedFile(filename, content))
        self.assertEqual(len(rows), len(sample_files.ASSET_SAMPLE_ROWS))
        first = rows[0]
        for field, expected in (('jda', '22032'), ('category', 'Desktop'),
                                ('brand_model', 'Lenovo-M70Q'), ('usage', 'xstore'),
                                ('status', 'In Store'), ('photo_requirement', '需要拍照')):
            self.assertEqual(
                str(kering_import._get(first, kering_import.ASSET_COLUMNS[field])).strip(),
                expected, field)

    def test_sample_headers_are_canonical_aliases(self):
        pairs = ((sample_files.SCHEDULE_SAMPLE_FIELDS, kering_import.SCHEDULE_COLUMNS),
                 (sample_files.ASSET_SAMPLE_FIELDS, kering_import.ASSET_COLUMNS))
        for fields, columns in pairs:
            for field in fields:
                header = sample_files.sample_headers(fields, columns)[fields.index(field)]
                self.assertEqual(header, columns[field][0])
                self.assertIn(header, columns[field])

    def test_sample_downloads_and_import_page_links(self):
        self.client.force_login(self.manager)
        for kind, expected_name in (('schedule', 'sample_schedule.csv'),
                                    ('assets', 'sample_asset_list.csv')):
            response = self.client.get(reverse('inspections:import_sample', args=[kind]))
            self.assertEqual(response.status_code, 200, kind)
            self.assertIn('text/csv', response['Content-Type'])
            self.assertIn(expected_name, response['Content-Disposition'])
            self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'), 'expected a UTF-8 BOM')
        self.assertEqual(
            self.client.get(reverse('inspections:import_sample', args=['nope'])).status_code, 404)
        page = self.client.get(reverse('inspections:batch_import'))
        self.assertContains(page, reverse('inspections:import_sample', args=['schedule']))
        self.assertContains(page, reverse('inspections:import_sample', args=['assets']))

    def test_importing_the_sample_schedule_applies_every_slot_rule(self):
        content, filename = sample_files.schedule_sample()
        kering_import.import_kering_master(
            SimpleUploadedFile(filename, content), None,
            company_name='Kering', company_code='KER', batch=None,
            auto_arrange=True, arrange_start=date(2026, 10, 6), arrange_end=date(2026, 10, 8),
        )
        inspections = {row.jda_code: row for row in StoreInspection.objects.all()}
        self.assertEqual(len(inspections), 4)
        # Undated co-located Beijing pair: consecutive slots, busier store (71) first.
        self.assertEqual(inspections['22032'].inspection_date, date(2026, 10, 6))
        self.assertEqual(inspections['22032'].slot, 'am')
        self.assertEqual(inspections['22152'].inspection_date, date(2026, 10, 6))
        self.assertEqual(inspections['22152'].slot, 'pm')
        # Dated pair: the explicit AM is honoured and takes the morning capacity,
        # so the blank-slot neighbour lands in the PM.
        self.assertEqual(inspections['38091'].slot, 'am')
        self.assertEqual(inspections['38076'].slot, 'pm')
        # Location contact/geography fields came from the sample rows.
        self.assertEqual(Location.objects.get(code='38076').city, 'Jinan')
