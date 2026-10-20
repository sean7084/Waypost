"""Bundle export: asset list + per-store reports + photos in one ZIP.

Mirrors the per-store deliverable layout produced by the legacy Feishu pipeline
(``<JDA> <Brand> <Store> Report Per Store.xlsx`` + ``Photo/…``) and adds a
cross-store ``asset_list.xlsx`` for per-week/batch consumption.

The ZIP is written to a spooled temporary file (spills to disk above 10 MB) so a
large batch never holds every photo in RAM, and is returned as a FileResponse.
"""
import io
import tempfile
import zipfile
from collections import Counter
from datetime import timedelta

from django.core.files import File

from inspections.services.asset_list_export import build_asset_list_workbook
from inspections.services.photo_naming import assign_photo_display_names
from inspections.services.report_generator import generate_inspection_report


def _safe(text):
    return ''.join('_' if ch in '<>:"/\\|?*' else ch for ch in str(text or '')).strip() or 'inspection'


def _store_folder(inspection):
    return _safe(inspection.store_label or f'{inspection.jda_code} {inspection.store_name}')


def _folder_names(inspections):
    """Per-inspection ZIP folder names mirroring the Feishu per-store layout.

    One store can legitimately appear twice in a bundle (AM and PM slots, or a
    re-visit), which would collide on ``<JDA> <Brand> <Store>`` and produce
    duplicate ZIP entries. Only the colliding stores get the inspection date and
    slot appended, so the common single-visit case keeps the clean folder name.
    """
    labels = [_store_folder(inspection) for inspection in inspections]
    counts = Counter(labels)
    names = []
    taken = set()
    for inspection, label in zip(inspections, labels):
        if counts[label] > 1:
            slot = (inspection.slot or '').upper()
            name = _safe(f'{label} ({inspection.inspection_date:%Y-%m-%d} {slot})')
        else:
            name = label
        suffix = 2
        while name in taken:
            name = _safe(f'{label}-{suffix}')
            suffix += 1
        taken.add(name)
        names.append(name)
    return names


# ``generate_inspection_report`` stamps ``report_generated_at`` and then saves the
# row, which bumps ``updated_at`` a few milliseconds later - so a freshly built
# report is always marginally "older" than ``updated_at``. The tolerance keeps
# that from reading as stale while still catching genuine post-report edits.
_FRESHNESS_TOLERANCE = timedelta(seconds=5)


def _report_is_stale(inspection):
    """True when the inspection was edited after its report was generated."""
    generated = inspection.report_generated_at
    if generated is None:
        return True
    updated = inspection.updated_at
    if updated is None:
        return False
    return generated < updated - _FRESHNESS_TOLERANCE


def _ensure_report(inspection):
    """Return the inspection with an up-to-date ``report_file``.

    Regenerates when the workbook is missing or stale so a bundle never ships an
    outdated report. Generation failures are swallowed: the rest of the bundle
    still ships.
    """
    if not inspection.report_file or _report_is_stale(inspection):
        try:
            generate_inspection_report(inspection)
            inspection.refresh_from_db()
        except Exception:
            return inspection
    return inspection


def build_bundle(inspections, *, include_asset_list=True, include_photos=True,
                 include_reports=True):
    """Write the bundle ZIP to a spooled temp file and return it (seeked to 0).

    Pair it with :func:`bundle_filename`, or use :func:`bundle_as_file_response`.
    """
    inspections = list(inspections)
    folders = _folder_names(inspections)
    tmp = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        if include_asset_list:
            workbook = build_asset_list_workbook(inspections)
            buffer = io.BytesIO()
            workbook.save(buffer)
            zf.writestr('asset_list.xlsx', buffer.getvalue())

        for inspection, folder in zip(inspections, folders):
            if include_reports:
                inspection = _ensure_report(inspection)
                if inspection.report_file:
                    # Canonical deliverable name: the stored file carries Django's
                    # uniqueness suffix, which is noise in a client-facing bundle.
                    report_name = _safe(
                        f'{inspection.store_label or folder} Report Per Store.xlsx'
                    )
                    with inspection.report_file.open('rb') as fh:
                        zf.writestr(f'{folder}/{report_name}', fh.read())

            if include_photos:
                photos = assign_photo_display_names(inspection)
                used = {}
                for photo in photos:
                    if not photo.image:
                        continue
                    base = photo.display_name or f'{photo.kind}-{photo.sort_index}'
                    count = used.get(base, 0)
                    used[base] = count + 1
                    name = base if count == 0 else f'{base.rsplit(".", 1)[0]}({count}).{base.rsplit(".", 1)[-1]}' if '.' in base else f'{base}({count})'
                    with photo.image.open('rb') as fh:
                        zf.writestr(f'{folder}/Photo/{name}', fh.read())

    tmp.seek(0)
    return tmp


def bundle_filename(inspections, batch=None):
    """Human-friendly ZIP name: batch name when scoped, else date-range."""
    if batch is not None:
        return f'{_safe(batch.name)}_Export.zip'
    inspections = list(inspections)
    if not inspections:
        return 'inspections_export.zip'
    dates = [i.inspection_date for i in inspections if i.inspection_date]
    if dates:
        return f'inspections_{min(dates):%Y%m%d}_{max(dates):%Y%m%d}_Export.zip'
    return 'inspections_export.zip'


def bundle_as_file_response(inspections, *, batch=None, include_asset_list=True,
                            include_photos=True, include_reports=True):
    """Convenience: build the bundle and wrap it in a Django File object."""
    from django.http import FileResponse

    tmp = build_bundle(
        inspections,
        include_asset_list=include_asset_list,
        include_photos=include_photos,
        include_reports=include_reports,
    )
    filename = bundle_filename(inspections, batch=batch)
    response = FileResponse(File(tmp, name=filename), as_attachment=True, filename=filename)
    return response


def photo_bundle_response(inspection):
    """Stream a photos-only ZIP for one inspection: ``<store> Photo.zip``.

    Replaces the retired ``photo_zip`` field. Named per-store (not the date-range
    ``bundle_filename``) so a single-store download reads like the old deliverable.
    """
    from django.http import FileResponse

    tmp = build_bundle(
        [inspection], include_asset_list=False, include_photos=True, include_reports=False,
    )
    label = inspection.store_label or inspection.jda_code or 'inspection'
    filename = f'{_safe(label)} Photo.zip'
    return FileResponse(File(tmp, name=filename), as_attachment=True, filename=filename)
