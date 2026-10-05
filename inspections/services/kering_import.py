"""
Reusable Kering master import (schedule + optional asset list).

Extracted from the ``import_kering_master`` management command so the web UI can
drive the same logic from uploaded files. Accepts file-like objects (Django
``UploadedFile`` or an opened binary handle) OR filesystem paths.

When ``assets_file`` is None the importer runs in schedule-only mode: it creates
the batch's ``StoreInspection`` shells but no expected ``InspectionDevice`` rows.
Engineers then add devices onsite via the mini program.

When ``batch`` is provided, every created/updated ``StoreInspection`` is attached
to it, giving the web UI a stable handle for "export this batch" / "show this
batch on the calendar".
"""
import os

from django.db import transaction

from assets.models import Asset, AssetBrand, AssetCategory, AssetModel
from companies.models import Company, Division, Location
from inspections.constants import (
    ASSET_PLACEHOLDER_PREFIX,
    PHOTO_NOT_REQUIRED_TAG,
    PHOTO_REQUIRED_TAG,
    is_placeholder_identifier,
    normalize_device_category,
)
from inspections.models import InspectionBatch, InspectionDevice, StoreInspection
from inspections.services.schedule_arranger import ScheduleCapacityError, arrange
from utils.import_rollback import finalize_import_run, record_import_change, start_import_run

SCHEDULE_COLUMNS = {
    'jda': ['JDA code', 'JDA', 'jda_code', 'jda'],
    'brand': ['Brand', 'brand', '品牌'],
    'store': ['Store Name', 'store_name', 'store', '店铺名称'],
    'date': ['inspection_date', 'date', 'Inspection Date', '日期'],
    'address': ['Address', 'address', '地址'],
    'city': ['City', 'city', '城市'],
    'phone': ['Store Dir. Phone', 'phone', 'Store Dir Phone', '电话'],
}
ASSET_COLUMNS = {
    'store': ['STORE', 'Store', 'store'],
    'jda': ['JDA', 'jda', 'JDA code'],
    'asset_id': ['Asset ID', 'asset_id', '资产编号'],
    'category': ['Category', 'category', '设备类型'],
    'brand_model': ['Brand-Model', 'brand_model', '设备型号'],
    'sn': ['SN', 'sn', '序列号'],
    'warranty_start': ['Warranty-start', 'warranty_start', 'Warranty Start'],
    'usage': ['Usage', 'usage'],
    'status': ['Status', 'status'],
    'photo_requirement': ['照片需求', 'photo_requirement', 'Photo Requirement'],
    'outline': ['大纲', 'outline'],
    'device_notes': ['注释', 'device_notes', 'notes'],
}


# -- value helpers -----------------------------------------------------------
def _clean(value):
    if value is None:
        return ''
    text = ' '.join(str(value).strip().split())
    return '' if text.lower() in ('nan', 'none', 'nat') else text


def _has_cjk(text):
    """True when the string contains CJK ideographs (used to pick chinese_address)."""
    return any('\u4e00' <= ch <= '\u9fff' for ch in str(text or ''))


def _clean_identifier(value):
    """Normalize an SN / Asset ID, clearing placeholder values to blank."""
    text = _clean(value)
    if is_placeholder_identifier(text):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return text


def _normalize_jda(value):
    text = _clean(value)
    if not text:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return text.split('.')[0] if text.replace('.', '', 1).isdigit() else text


def _parse_date(value):
    """Parse a workbook cell into a date; return None for blanks/NaN/NaT."""
    import pandas as pd
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, 'date'):
        try:
            parsed = value.date()
        except Exception:
            parsed = None
        if parsed is not None and not pd.isna(parsed):
            return parsed
    text = _clean(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors='coerce')
    try:
        if pd.isna(parsed):
            return None
    except (TypeError, ValueError):
        pass
    return parsed.date()


def _split_brand_model(brand_model):
    """Best-effort split of a Kering 'Brand-Model' string into (brand, model)."""
    text = _clean(brand_model)
    if not text:
        return 'Unknown', ''
    if '-' in text:
        brand, _, model = text.partition('-')
        return (brand.strip() or 'Unknown'), model.strip()
    parts = text.split(' ', 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text, ''


def _category_code(category_name):
    base = ''.join(ch for ch in category_name if ch.isalnum())[:12].upper()
    return base or 'CAT'


def _get(row, candidates):
    for key in candidates:
        if key in row and row[key] is not None:
            return row[key]
    return ''


# -- file reading ------------------------------------------------------------
def _detect_extension(source):
    """Best-effort filename extension for a path or UploadedFile."""
    name = getattr(source, 'name', None) or (source if isinstance(source, str) else '')
    return os.path.splitext(str(name))[1].lower()


def _read_rows(source):
    """Read a workbook/CSV into a list of dict rows.

    ``source`` may be a filesystem path (str) or a file-like object. For
    file-like objects we use the ``.name`` attribute to detect csv vs xlsx.
    """
    import pandas as pd

    ext = _detect_extension(source)

    # Path-like: open it ourselves.
    if isinstance(source, (str, os.PathLike)):
        if ext == '.csv':
            from utils.csv_import import read_csv_rows_with_fallback
            with open(source, 'rb') as handle:
                reader, _encoding = read_csv_rows_with_fallback(handle)
                return list(reader)
        dataframe = pd.read_excel(source)
        return dataframe.to_dict('records')

    # File-like (Django UploadedFile or opened handle).
    if ext == '.csv':
        from utils.csv_import import read_csv_rows_with_fallback
        # Ensure we read from the start; UploadedFile may have been touched.
        if hasattr(source, 'seek'):
            try:
                source.seek(0)
            except Exception:
                pass
        reader, _encoding = read_csv_rows_with_fallback(source)
        return list(reader)

    # Excel via pandas; accepts a file-like binary object.
    if hasattr(source, 'seek'):
        try:
            source.seek(0)
        except Exception:
            pass
    dataframe = pd.read_excel(source)
    return dataframe.to_dict('records')


# -- asset helpers -----------------------------------------------------------
def _ensure_asset(company, inspection, category_raw, brand_model, sn, asset_id,
                  warranty_start, stats, recorder):
    """Get-or-create the Asset (and its category/brand/model) for a device row."""
    category_name = (normalize_device_category(category_raw) or category_raw or 'Other')[:255]
    cat_code = _category_code(category_name)
    category = (
        AssetCategory.objects.filter(code=cat_code).first()
        or AssetCategory.objects.filter(name=category_name).first()
    )
    cat_created = category is None
    if cat_created:
        category = AssetCategory.objects.create(name=category_name, code=cat_code)
        stats['categories'] += 1
    recorder(category, cat_created)

    brand_name, model_name = _split_brand_model(brand_model)
    brand_name = brand_name[:255]
    brand_code = (brand_name[:20].upper().replace(' ', '') or 'NA')
    brand = (
        AssetBrand.objects.filter(code=brand_code).first()
        or AssetBrand.objects.filter(name=brand_name).first()
    )
    brand_created = brand is None
    if brand_created:
        brand = AssetBrand.objects.create(name=brand_name, code=brand_code)
        stats['brands'] += 1
    recorder(brand, brand_created)

    model = None
    if model_name:
        model, model_created = AssetModel.objects.get_or_create(
            brand=brand, model_number=model_name[:100],
            defaults={'name': model_name[:255], 'category': category},
        )
        if model_created:
            stats['models'] += 1
        recorder(model, model_created)

    lookup_sn = '' if is_placeholder_identifier(sn) else sn
    clean_asset_id = '' if is_placeholder_identifier(asset_id) else asset_id
    asset_defaults = {
        'category': category, 'brand': brand, 'model': model,
        'division': inspection.division, 'location': inspection.location,
        'warranty_start_date': warranty_start, 'status': Asset.AssetStatus.AVAILABLE,
    }
    asset_id_to_use = clean_asset_id
    if asset_id_to_use and Asset.objects.filter(company=company, asset_number=asset_id_to_use).exists():
        asset_id_to_use = ''

    if lookup_sn:
        asset, asset_created = Asset.objects.get_or_create(
            company=company, serial_number=lookup_sn,
            defaults={**asset_defaults, 'asset_number': asset_id_to_use},
        )
    elif asset_id_to_use:
        asset, asset_created = Asset.objects.get_or_create(
            company=company, asset_number=asset_id_to_use,
            defaults={**asset_defaults, 'serial_number': ''},
        )
    else:
        asset = Asset.objects.create(
            company=company, serial_number='', asset_number='', **asset_defaults,
        )
        asset_created = True
    if asset_created:
        stats['assets'] += 1
    recorder(asset, asset_created)
    return asset


# -- main entry point --------------------------------------------------------
def import_kering_master(schedule_file, assets_file=None, *,
                         company_name='Kering', company_code='KER',
                         engineer=None, batch=None, user=None,
                         dry_run=False, track_rollback=True,
                         auto_arrange=False, arrange_start=None, arrange_end=None):
    """Import a Kering schedule (+ optional master asset list) into Waypost.

    Args:
        schedule_file: path or file-like for schedule.xlsx (required).
        assets_file: path or file-like for asset_list.xlsx (optional). When
            None, runs in schedule-only mode (no expected devices created).
        company_name / company_code: client company identity (defaults: Kering).
        engineer: optional ``User`` to assign as onsite engineer.
        batch: optional ``InspectionBatch`` to attach every inspection to. When
            None, a batch is NOT auto-created (caller decides).
        user: optional ``User`` recorded as the import initiator (rollback owner).
        dry_run: when True, roll back the transaction after reporting.
        track_rollback: when False, skip ImportRun tracking (faster for big runs).
        auto_arrange: when True and schedule rows lack ``inspection_date``, assign
            dates+slots via ``schedule_arranger.arrange`` (city clustering, same
            address consecutive AM/PM) over [arrange_start, arrange_end].
        arrange_start / arrange_end: the scheduling window for auto_arrange.

    Returns:
        dict with keys: ``stats`` (counters), ``per_store`` (jda -> summary),
        ``import_run`` (ImportRun or None), ``batch`` (InspectionBatch or None).
    """
    schedule_rows = _read_rows(schedule_file)
    asset_rows = _read_rows(assets_file) if assets_file is not None else []

    # Pre-compute auto-arranged (date, slot) for rows missing an inspection_date.
    arranged_by_index = {}
    if auto_arrange and arrange_start and arrange_end:
        missing = [
            i for i, row in enumerate(schedule_rows)
            if _normalize_jda(_get(row, SCHEDULE_COLUMNS['jda']))
            and _parse_date(_get(row, SCHEDULE_COLUMNS['date'])) is None
        ]
        if missing:
            sub_rows = [
                {
                    'city': _clean(_get(schedule_rows[i], SCHEDULE_COLUMNS['city'])),
                    'address': _clean(_get(schedule_rows[i], SCHEDULE_COLUMNS['address'])),
                }
                for i in missing
            ]
            arranged = arrange(sub_rows, arrange_start, arrange_end)
            for i, item in zip(missing, arranged):
                arranged_by_index[i] = (item['inspection_date'], item['slot'])

    stats = {
        'companies': 0, 'divisions': 0, 'locations': 0, 'inspections': 0,
        'categories': 0, 'brands': 0, 'models': 0, 'assets': 0, 'devices': 0,
        'devices_skipped_duplicate': 0,
    }
    created_uids = {key: set() for key in stats}
    per_store = {}

    # Rollback tracking state (closure-style recorder so helpers stay simple).
    seq_holder = {'n': 0}
    run_holder = {'run': None}
    if not dry_run and track_rollback:
        run_holder['run'] = start_import_run(
            user, 'inspections', 'kering_master', total_rows=len(asset_rows) or len(schedule_rows)
        )

    def _record(instance, was_created):
        if was_created and run_holder['run'] is not None:
            seq_holder['n'] += 1
            record_import_change(run_holder['run'], seq_holder['n'], 'create', instance)

    with transaction.atomic():
        company, company_created = Company.objects.get_or_create(
            name=company_name,
            defaults={'code': company_code, 'status': Company.CompanyStatus.ACTIVE},
        )
        if company_created:
            stats['companies'] += 1
        _record(company, company_created)

        # Pass 1: schedule -> divisions (brands), locations (stores), inspections.
        inspection_by_jda = {}
        for row_index, row in enumerate(schedule_rows):
            jda = _normalize_jda(_get(row, SCHEDULE_COLUMNS['jda']))
            if not jda:
                continue
            brand = _clean(_get(row, SCHEDULE_COLUMNS['brand']))
            store = _clean(_get(row, SCHEDULE_COLUMNS['store']))
            address = _clean(_get(row, SCHEDULE_COLUMNS['address']))
            city = _clean(_get(row, SCHEDULE_COLUMNS['city']))
            phone = _clean(_get(row, SCHEDULE_COLUMNS['phone']))
            inspection_date = _parse_date(_get(row, SCHEDULE_COLUMNS['date']))
            slot = StoreInspection.Slot.AM
            if inspection_date is None and row_index in arranged_by_index:
                inspection_date, slot = arranged_by_index[row_index]

            division = None
            if brand:
                division, div_created = Division.objects.get_or_create(
                    company=company, name=brand, defaults={'code': brand[:20]},
                )
                if div_created:
                    stats['divisions'] += 1
                _record(division, div_created)

            location, loc_created = Location.objects.get_or_create(
                company=company, code=jda,
                defaults={
                    'name': store or jda,
                    'division': division,
                    'location_type': Location.LocationType.STORE,
                    'address_line1': address,
                    'city': city,
                    'phone_number': phone[:17],
                    'chinese_address': address if _has_cjk(address) else '',
                },
            )
            if loc_created:
                stats['locations'] += 1
            else:
                # Refresh previously-missing contact/geography fields on re-import
                # without clobbering values an operator already set.
                refresh = {}
                if address and not location.address_line1:
                    refresh['address_line1'] = address
                    if _has_cjk(address) and not location.chinese_address:
                        refresh['chinese_address'] = address
                if city and not location.city:
                    refresh['city'] = city
                if phone and not location.phone_number:
                    refresh['phone_number'] = phone[:17]
                if refresh:
                    Location.objects.filter(pk=location.pk).update(**refresh)
            _record(location, loc_created)

            store_label = ' '.join(part for part in [jda, brand, store] if part).strip()
            if inspection_date is None:
                # Skip rows without a valid date (and no auto-arrange window);
                # the caller can inspect per_store to see what landed.
                continue

            defaults = {
                'company': company, 'division': division, 'jda_code': jda,
                'brand_name': brand, 'store_name': store, 'store_label': store_label,
                'engineer': engineer, 'status': StoreInspection.Status.PLANNED,
                'slot': slot,
            }
            if batch is not None:
                defaults['batch'] = batch

            inspection, insp_created = StoreInspection.objects.get_or_create(
                location=location, inspection_date=inspection_date,
                defaults=defaults,
            )
            # If the inspection already existed but has no batch and one was
            # supplied, attach it (idempotent re-import into the same batch).
            if not insp_created and batch is not None and inspection.batch_id != batch.id:
                inspection.batch = batch
                inspection.save(update_fields=['batch', 'updated_at'])
            if insp_created:
                stats['inspections'] += 1
            _record(inspection, insp_created)
            inspection_by_jda[jda] = inspection
            per_store[jda] = {'store': store_label, 'devices': 0}

        # Pass 2 (optional): master asset list -> assets + expected devices.
        if asset_rows:
            placeholder_seq = 0
            for row in asset_rows:
                jda = _normalize_jda(_get(row, ASSET_COLUMNS['jda']))
                inspection = inspection_by_jda.get(jda)
                if inspection is None:
                    continue

                category_raw = _clean(_get(row, ASSET_COLUMNS['category']))
                brand_model = _clean(_get(row, ASSET_COLUMNS['brand_model']))
                sn = _clean_identifier(_get(row, ASSET_COLUMNS['sn']))
                asset_id = _clean_identifier(_get(row, ASSET_COLUMNS['asset_id']))

                asset = _ensure_asset(
                    company=company, inspection=inspection, category_raw=category_raw,
                    brand_model=brand_model, sn=sn, asset_id=asset_id,
                    warranty_start=_parse_date(_get(row, ASSET_COLUMNS['warranty_start'])),
                    stats=stats, recorder=_record,
                )

                device_sn = sn
                if not device_sn and not asset_id:
                    placeholder_seq += 1
                    device_sn = f'{ASSET_PLACEHOLDER_PREFIX}-{placeholder_seq:06d}'

                uid = f'{jda}:{asset_id or device_sn or asset.asset_number}'
                if uid in created_uids['devices']:
                    stats['devices_skipped_duplicate'] += 1
                    continue
                created_uids['devices'].add(uid)

                photo_requirement = _clean(_get(row, ASSET_COLUMNS['photo_requirement']))
                photo_required = photo_requirement != PHOTO_NOT_REQUIRED_TAG
                if photo_requirement == PHOTO_REQUIRED_TAG:
                    photo_required = True

                _device, device_created = InspectionDevice.objects.update_or_create(
                    store_inspection=inspection, client_device_uid=uid,
                    defaults={
                        'asset': asset,
                        'asset_id_text': asset_id,
                        'category': normalize_device_category(category_raw),
                        'brand_model': brand_model,
                        'sn': device_sn,
                        'usage': _clean(_get(row, ASSET_COLUMNS['usage'])),
                        'warranty_start': _parse_date(_get(row, ASSET_COLUMNS['warranty_start'])),
                        'photo_required': photo_required,
                        'outline': _clean(_get(row, ASSET_COLUMNS['outline'])),
                        'device_notes': _clean(_get(row, ASSET_COLUMNS['device_notes'])),
                        'status': InspectionDevice.Status.IN_STORE,
                        'is_new_device': False,
                    },
                )
                if device_created:
                    stats['devices'] += 1
                    if jda in per_store:
                        per_store[jda]['devices'] += 1
                _record(_device, device_created)

        if run_holder['run'] is not None:
            finalize_import_run(
                run_holder['run'], created=seq_holder['n'], updated=0,
                skipped=stats['devices_skipped_duplicate'],
                notes='Kering master import (schedule + asset list)',
            )
            # Link the ImportRun back to the batch for traceability.
            if batch is not None and batch.import_run_id != run_holder['run'].id:
                batch.import_run = run_holder['run']
                batch.save(update_fields=['import_run', 'updated_at'])

        if dry_run:
            transaction.set_rollback(True)

    return {
        'stats': stats,
        'per_store': per_store,
        'import_run': run_holder['run'],
        'batch': batch,
    }
