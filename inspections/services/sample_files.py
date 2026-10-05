"""Sample schedule / asset-list CSV files for the batch import form.

The headers are generated from the importer's own column maps, so a sample can
never drift from what :mod:`inspections.services.kering_import` actually parses:
each header is the canonical (first) alias of a ``SCHEDULE_COLUMNS`` /
``ASSET_COLUMNS`` entry, and ``inspections.tests_import_scheduling`` round-trips
these samples back through the importer.

Files are written as UTF-8 with a BOM because the asset list has Chinese headers
(照片需求 / 大纲 / 注释) that Excel would otherwise mangle.
"""
import csv
import io

from inspections.services.kering_import import ASSET_COLUMNS, SCHEDULE_COLUMNS

# Column order of each sample, as keys of the importer's column maps.
SCHEDULE_SAMPLE_FIELDS = (
    'brand', 'store', 'jda', 'city', 'address', 'phone', 'date', 'slot', 'device_count',
)
ASSET_SAMPLE_FIELDS = (
    'store', 'jda', 'asset_id', 'category', 'brand_model', 'sn', 'warranty_start',
    'usage', 'status', 'photo_requirement', 'outline', 'device_notes',
)

# Four rows that between them exercise every scheduling rule:
#   * two stores in one mall with no date -> auto-arranged onto consecutive AM/PM
#     slots of the same day, the busier store in the morning;
#   * a row with both date and slot -> used exactly as written;
#   * a row with a date but no slot -> balanced across that day's AM/PM.
SCHEDULE_SAMPLE_ROWS = (
    {'brand': 'Gucci', 'store': 'Beijing SKP Store', 'jda': '22032', 'city': 'Beijing',
     'address': 'No.87 Jian Guo Road, Beijing SKP', 'phone': '+8610 65981606',
     'date': '', 'slot': '', 'device_count': '71'},
    {'brand': 'Gucci', 'store': 'Beijing SKP Pop', 'jda': '22152', 'city': 'Beijing',
     'address': 'No.87 Jian Guo Road, Beijing SKP', 'phone': '+8610 65000957',
     'date': '', 'slot': '', 'device_count': '18'},
    {'brand': 'YSL', 'store': 'Shanghai Qiantan', 'jda': '38091', 'city': 'Shanghai',
     'address': 'Qiantan Avenue 1', 'phone': '+8621 11112222',
     'date': '2026-10-05', 'slot': 'AM', 'device_count': '30'},
    {'brand': 'YSL', 'store': 'Jinan Guihe popup', 'jda': '38076', 'city': 'Jinan',
     'address': 'Guihe Shopping Center', 'phone': '+86531 88886666',
     'date': '2026-10-05', 'slot': '', 'device_count': '12'},
)

ASSET_SAMPLE_ROWS = (
    {'store': 'Beijing SKP Store', 'jda': '22032', 'asset_id': 'CGDR-0001',
     'category': 'Desktop', 'brand_model': 'Lenovo-M70Q', 'sn': 'PC1SN0001',
     'warranty_start': '2024-03-01', 'usage': 'xstore', 'status': 'In Store',
     'photo_requirement': '需要拍照', 'outline': '整体照片 + 序列号照片', 'device_notes': ''},
    {'store': 'Beijing SKP Store', 'jda': '22032', 'asset_id': 'CGDR-0002',
     'category': 'Monitor', 'brand_model': 'Lenovo-L24i', 'sn': 'MON1SN0002',
     'warranty_start': '2024-03-01', 'usage': 'xstore', 'status': 'In Store',
     'photo_requirement': '需要拍照', 'outline': '整体照片', 'device_notes': ''},
    {'store': 'Shanghai Qiantan', 'jda': '38091', 'asset_id': 'CGDR-0003',
     'category': 'Printer', 'brand_model': 'Epson-TM-T88', 'sn': 'PRN1SN0003',
     'warranty_start': '2023-11-15', 'usage': 'label', 'status': 'Not In Store',
     'photo_requirement': '无需拍照', 'outline': '', 'device_notes': '门店反馈已送修，现场未见'},
)


def sample_headers(fields, columns):
    """Canonical header names for a sample, taken from the importer's column maps."""
    return [columns[field][0] for field in fields]


def _render(fields, columns, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(sample_headers(fields, columns))
    for row in rows:
        writer.writerow([row.get(field, '') for field in fields])
    return buffer.getvalue().encode('utf-8-sig')


def schedule_sample():
    """Return ``(content_bytes, filename)`` for a sample site list / schedule."""
    return _render(SCHEDULE_SAMPLE_FIELDS, SCHEDULE_COLUMNS, SCHEDULE_SAMPLE_ROWS), \
        'sample_schedule.csv'


def asset_list_sample():
    """Return ``(content_bytes, filename)`` for a sample master asset list."""
    return _render(ASSET_SAMPLE_FIELDS, ASSET_COLUMNS, ASSET_SAMPLE_ROWS), \
        'sample_asset_list.csv'


# Download kind -> generator, used by the import form's sample links.
SAMPLES = {
    'schedule': schedule_sample,
    'assets': asset_list_sample,
}
