"""
Historical photo reuse (DB-native port of the EUS historical-photo step).

The legacy pipeline copied prior-year photos from network archives by serial-number
match and flipped the target's photo-requirement tag to 无需拍照. Here prior
inspections already live in the database, so we copy reusable photos from a previous
`StoreInspection` for the same store, keyed by normalized SN, and mark the target
device `photo_required=False` so the engineer need not re-shoot it onsite.

Idempotent: a device that already has a photo of a given kind is skipped, so running
twice does not duplicate photos.
"""
import logging

from django.core.files.base import ContentFile

from inspections.models import InspectionPhoto

from .thumbnails import ensure_thumbnail

LOGGER = logging.getLogger(__name__)

REUSABLE_KINDS = (InspectionPhoto.Kind.OVERALL, InspectionPhoto.Kind.SERIAL)


def _sn_key(sn):
    """Compact, case-insensitive SN key (mirrors the EUS normalize_sn_key intent)."""
    return ''.join(str(sn or '').split()).upper()


def _find_source_inspection(target):
    """Most recent prior inspection for the same store, if any."""
    return (
        type(target).objects.filter(location_id=target.location_id)
        .exclude(pk=target.pk)
        .order_by('-inspection_date', '-created_at')
        .first()
    )


def reuse_historical_photos(target_inspection, source_inspection=None, kinds=REUSABLE_KINDS):
    """Copy reusable photos from a prior inspection into `target_inspection`.

    Returns a summary dict: {source_inspection, devices_matched, photos_copied}.
    """
    if source_inspection is None:
        source_inspection = _find_source_inspection(target_inspection)

    result = {'source_inspection': None, 'devices_matched': 0, 'photos_copied': 0}
    if source_inspection is None:
        return result
    result['source_inspection'] = str(source_inspection.id)

    # Index source devices by SN key.
    source_devices = {}
    for device in source_inspection.devices.exclude(sn=''):
        key = _sn_key(device.sn)
        if key:
            source_devices.setdefault(key, device)

    for target_device in target_inspection.devices.exclude(sn=''):
        source_device = source_devices.get(_sn_key(target_device.sn))
        if source_device is None:
            continue

        copied = 0
        for photo in source_device.photos.filter(kind__in=kinds):
            # Skip kinds the target already has (keeps reruns idempotent).
            if target_device.photos.filter(kind=photo.kind).exists():
                continue
            new_photo = InspectionPhoto(
                store_inspection=target_inspection,
                device=target_device,
                kind=photo.kind,
                sort_index=photo.sort_index,
                client_photo_uid=f'hist-{photo.id}',
            )
            try:
                photo.image.open('rb')
                data = photo.image.read()
            finally:
                photo.image.close()
            ext = photo.image.name.rsplit('.', 1)[-1] if '.' in photo.image.name else 'jpg'
            new_photo.image.save(f'hist_{photo.id}.{ext}', ContentFile(data), save=False)
            new_photo.save()
            try:
                ensure_thumbnail(new_photo)
            except Exception:
                LOGGER.warning('Thumbnail step failed for reused photo %s', new_photo.pk, exc_info=True)
            copied += 1

        if copied:
            target_device.photo_required = False
            target_device.save(update_fields=['photo_required', 'updated_at'])
            result['devices_matched'] += 1
            result['photos_copied'] += copied

    return result
