"""Shared helpers for tests that write media files.

Why this module exists (go-live blocker 6): eight test classes each carried their
own ``@override_settings(MEDIA_ROOT=tempfile.mkdtemp(...))`` and five more read
``FieldFile.path``. Both pin ``STORAGES['default']`` to ``FileSystemStorage``, so
switching the project to an object-storage backend would have turned the suite
red without asserting anything about the code under test.

``IsolatedMediaTestCase`` swaps the default backend for Django's in-memory one,
which keeps exercising the *storage API* (``save``/``exists``/``open``/``size``/
``delete``/``listdir``) and raises on ``.path``. That is the point: a filesystem
dependency now fails loudly in the test that has it instead of quietly writing
into the developer's real ``media/`` tree.
"""
import io
from contextlib import ExitStack

from django.conf import settings
from django.test import TestCase, override_settings
from PIL import Image

# Only 'default' changes; 'staticfiles' is copied through so WhiteNoise's
# manifest backend (when DEBUG is off) keeps working for view tests.
#
# base_url is not decoration: InMemoryStorage raises on ``url()`` without one, and
# the media-facing code paths under test all end in a URL (``FieldFile.url`` via a
# DRF FileField, ``thumbnail_url_for``, ``absolute_media_url``). Real deployments
# set MEDIA_URL to '/media/' or to the bucket's public base, so the test backend
# mirrors whichever of those the current settings picked.
MEDIA_STORAGE_OVERRIDES = {
    'STORAGES': {
        **settings.STORAGES,
        'default': {
            'BACKEND': 'django.core.files.storage.InMemoryStorage',
            'OPTIONS': {'base_url': settings.MEDIA_URL},
        },
    },
}


def png_bytes(color='red', size=(8, 8)):
    """PNG bytes for a solid-colour image, for upload and storage fixtures."""
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


class IsolatedMediaTestCase(TestCase):
    """TestCase whose media writes go to memory, never to ``MEDIA_ROOT``.

    The override is enabled for the whole class rather than per test, matching the
    ``MEDIA_ROOT=tempfile.mkdtemp()`` decorators it replaces: those directories
    were shared by every test in the class, and a test that counts files in an
    upload folder relies on that. Enabling in ``setUpClass`` (instead of the
    decorator) also survives subclasses that define ``setUp`` without calling
    ``super()`` — which several do.
    """

    @classmethod
    def setUpClass(cls):
        cls._storage_stack = ExitStack()
        cls._storage_stack.enter_context(override_settings(**MEDIA_STORAGE_OVERRIDES))
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._storage_stack.close()
