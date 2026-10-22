"""JPEG thumbnails for inspection photos, rendered on upload.

Why on upload and not on read: an on-read thumbnail is a Django view pulling the
original bytes back out of object storage, resizing them and re-uploading - which
re-creates exactly the ECS egress and per-request CPU the OSS cutover exists to
remove, and puts a resize in the middle of the mini program's photo list. On
upload, the bytes are already in memory (the request is writing them anyway) and
one PIL resize is the whole cost.

Why derived naming instead of a model field: ``thumbnail_storage_name()`` computes
the path from the photo's own identity, so there is no column to migrate, no
second value to keep in sync when a photo is replaced, and no second
orphan-file-deletion path to get wrong (the bug PR #97 fixed for ``photo_zip``).
The cost is that every photo written before this change has no derivative, which
is why ``thumbnail_url_for()`` falls back to the original and a backfill command
exists.
"""
import io
import logging

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

LOGGER = logging.getLogger(__name__)

# 480 px long edge at JPEG quality 75 is ~30 KB for a phone photo: enough for the
# 80px-high grid cell in the web detail page and the mini program's list, and two
# orders of magnitude below the ~300 KB original.
THUMBNAIL_LONG_EDGE = 480
THUMBNAIL_QUALITY = 75


def thumbnail_storage_name(photo):
    """Storage name of a photo's derivative: a ``thumbs/`` sibling of ``photos/``."""
    return f'inspections/{photo.store_inspection_id}/thumbs/{photo.id}.jpg'


def render_thumbnail_bytes(f):
    """Return JPEG bytes for file-like ``f``: long edge <= 480, alpha on white.

    Raises on undecodable input - callers in the write path must catch it (see
    ``ensure_thumbnail``).
    """
    with Image.open(f) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            # JPEG has no alpha channel; compositing onto white beats PIL's
            # default of dropping it to black on a transparent photo.
            flattened = Image.new('RGB', image.size, (255, 255, 255))
            rgba = image.convert('RGBA')
            flattened.paste(rgba, mask=rgba.getchannel('A'))
            image = flattened
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        # thumbnail() preserves aspect ratio and only ever shrinks.
        image.thumbnail((THUMBNAIL_LONG_EDGE, THUMBNAIL_LONG_EDGE))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=THUMBNAIL_QUALITY, optimize=True)
    return buffer.getvalue()


def ensure_thumbnail(photo):
    """Render and store a photo's derivative; True when it is on storage.

    Never raises. The original upload is the deliverable and a thumbnail is a
    convenience, so a decode failure, a missing file or a storage error must not
    turn a successful photo write into a failed one; the caller's ``try`` is a
    belt-braces duplicate of the guard here.
    """
    name = thumbnail_storage_name(photo)
    if not photo.image or not photo.image.name:
        return False
    try:
        with photo.image.open('rb') as image_file:
            data = render_thumbnail_bytes(image_file)
        if default_storage.exists(name):
            # save() would quietly get_available_name() a suffixed twin instead
            # of replacing the stale derivative of a re-uploaded photo.
            default_storage.delete(name)
        default_storage.save(name, ContentFile(data, name=name))
        # Refresh the memo, so the row that just got its derivative never answers
        # "no thumbnail" afterwards (see thumbnail_url_for).
        photo._thumbnail_available = True
        return True
    except Exception:
        LOGGER.warning('Could not write thumbnail %s for photo %s', name, photo.pk, exc_info=True)
        return False


def thumbnail_url_for(photo):
    """URL of the derivative, or of the original when it has none.

    The fallback is mandatory, not cosmetic: every photo stored before this
    pipeline exists returns False here, and the backfill command is what turns
    that off over time. ``exists()`` is memoised on the row instance - a list view
    serialises each photo once, so this only suppresses the double read when a
    template and a serializer both ask - and :func:`ensure_thumbnail` refreshes
    the memo so a row that has just been given a derivative never reports False.
    """
    cached = getattr(photo, '_thumbnail_available', None)
    if cached is None:
        try:
            cached = default_storage.exists(thumbnail_storage_name(photo))
        except Exception:
            # A storage error here must not 500 a photo list: fall back to the
            # original, which the caller is going to render either way.
            LOGGER.warning('Could not check thumbnail for photo %s', photo.pk, exc_info=True)
            cached = False
        photo._thumbnail_available = cached
    if cached:
        return default_storage.url(thumbnail_storage_name(photo))
    return photo.image.url
