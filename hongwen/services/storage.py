"""Private file storage for Hongwen uploads, TTS audio and KB attachments.

Nginx serves ``MEDIA_ROOT`` publicly through an ``alias /media/`` block
(docker/nginx/waypost.conf), so anything written to the default storage backend
is world-readable. Hongwen customer uploads (voice messages, photos), generated
TTS audio and knowledge-base attachments must NOT be public: they are served
through authenticated Django views (``FileResponse`` + ``storage.open``) rather
than a static alias. This module therefore pins a dedicated ``FileSystemStorage``
rooted at ``HONGWEN_PRIVATE_MEDIA_ROOT``, deliberately outside ``MEDIA_ROOT`` and
outside the default (possibly OSS) backend.

The storage is exposed as a *callable* so ``FileField`` resolves it lazily at
save time: settings are guaranteed ready then, and tests can override the root
with ``override_settings(HONGWEN_PRIVATE_MEDIA_ROOT=...)``.

Never resolve these files to a local filesystem path - reach them through the
storage API (``storage.open`` / ``save`` / ``delete`` / ``exists``), per the
repo-hygiene gate in tests/test_repo_hygiene.py.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


def private_storage():
    """Return the Hongwen private FileSystemStorage (lazy settings read).

    A fresh instance per call is fine: FileSystemStorage is stateless apart from
    its location, and Django caches the resolved storage on the field.
    """
    return FileSystemStorage(location=settings.HONGWEN_PRIVATE_MEDIA_ROOT)