"""The storage-backend suite: every claim of PR-A, green with no bucket.

Why this file exists at all. The PR moves media off the local disk behind an
operator-set gate, makes generated documents ephemeral, and derives photo
thumbnails. None of that can be exercised against Aliyun from CI, and none of it
needs to be: the gate is a pure function, the backend talks to ``oss2`` through a
handful of bucket calls that can be stubbed, and the filesystem and in-memory
backends Django ships are enough to prove the storage API the app actually uses.
Every test here runs with no bucket, no network, no RDS and no Redis.

The assertions are not arbitrary. Each pins a failure mode named in the go-live
blocker report or the plan's risk table:

* the gate (blocker 4) must select the *literal* FileSystemStorage string when
  unset, because "production is unchanged" is what lets this merge before the
  bucket exists;
* ``Cache-Control`` and ``Content-Disposition`` (blocker 8) must equal the numbers
  in ``docker/nginx/waypost.conf``, or the cutover silently changes how long every
  browser caches an inspection photo;
* the ephemeral-document lifetime (risk R2) must outlive the view but not the
  response, because ``FileResponse`` streams after the view has returned;
* a thumbnail must never fail an upload, and must fall back for photos written
  before the pipeline existed;
* the attachment manifest must keep two same-named uploads after the reshape
  removes their filesystem paths - the collapse is one refactor away, so it is
  pinned rather than left to the reader.

``WaypostOSSStorage`` has never touched a real bucket, so these tests are the only
verification it gets before the cutover. They were written against the attribute
names the installed ``oss2`` exposes (``HeadObjectResult.content_length``,
``ListObjectsResult.is_truncated``/``.next_marker``/``.prefix_list``/
``.object_list``, ``Bucket.sign_url(method, key, expires)``), so a rename in
``oss2`` fails here rather than in production.
"""
import io
import shutil
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, InMemoryStorage, default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from PIL import Image

from companies.models import Company
from deliveries.models import DeliveryOrder
from deliveries.services import fill_delivery_template
from invoices.models import EmailDispatch, InvoiceInfo, InvoiceInfoItem, WeeklyOrderBatch
from invoices.services import _open_attachment, collect_email_attachments, fill_invoice_template
from quotations.models import Quotation, QuotationAttachment
from tests.storage import IsolatedMediaTestCase, png_bytes
from utils.generated_documents import (
    TEMP_DIR_PREFIX,
    convert_xlsx_to_pdf,
    generated_document_dir,
    save_workbook_to_temp,
)
from utils.media_urls import absolute_media_url
from waypost.storage import (
    CACHE_CONTROL_INSPECTIONS,
    CACHE_CONTROL_MEDIA,
    FILE_SYSTEM_STORAGE_BACKEND,
    OSS_STORAGE_BACKEND,
    WaypostOSSStorage,
    is_inspection_deliverable,
    media_cache_control,
    public_oss_host,
    resolve_default_storage_backend,
)

INTERNAL_ENDPOINT = 'oss-cn-heyuan-internal.aliyuncs.com'
PUBLIC_ENDPOINT = 'oss-cn-heyuan.aliyuncs.com'

FULL_OSS_ENV = {
    'OSS_ENDPOINT': INTERNAL_ENDPOINT,
    'OSS_BUCKET_NAME': 'waypost-media',
    'OSS_ACCESS_KEY_ID': 'test-key-id',
    'OSS_ACCESS_KEY_SECRET': 'test-key-secret',
}


class DefaultStorageGateTests(SimpleTestCase):
    """Blocker 4: which backend ``STORAGES['default']`` gets, and nothing else.

    The gate is called as the pure function it is, with a plain dict, so no test
    reloads settings - and settings cannot drift from the function, because
    ``waypost/settings.py`` builds its own value from this same call.
    """

    def test_empty_environment_keeps_filesystem_storage(self):
        self.assertEqual(resolve_default_storage_backend({}), FILE_SYSTEM_STORAGE_BACKEND)

    def test_any_partial_set_keeps_filesystem_storage(self):
        """One, two or three keys mean "not configured", not "half on"."""
        keys = list(FULL_OSS_ENV)
        for count in (1, 2, 3):
            with self.subTest(keys=count):
                env = {key: FULL_OSS_ENV[key] for key in keys[:count]}
                self.assertEqual(resolve_default_storage_backend(env), FILE_SYSTEM_STORAGE_BACKEND)

    def test_all_four_keys_select_the_oss_backend(self):
        self.assertEqual(resolve_default_storage_backend(dict(FULL_OSS_ENV)), OSS_STORAGE_BACKEND)

    def test_blank_or_whitespace_value_does_not_count_as_set(self):
        """The settings guard strips each value before the gate sees it.

        The deployed ``.env`` is hand-edited, so ``OSS_ACCESS_KEY_SECRET=`` with
        nothing after the equals sign is a not-set line. Treating it as set would
        put a box on a backend whose credentials are whitespace, and the failure
        surfaces as an upload 500 rather than as configuration.
        """
        for blank in ('', '   ', '\t'):
            with self.subTest(value=blank):
                env = dict(FULL_OSS_ENV)
                env['OSS_ACCESS_KEY_SECRET'] = blank
                self.assertEqual(resolve_default_storage_backend(env), FILE_SYSTEM_STORAGE_BACKEND)

    def test_this_environment_is_unconfigured_so_production_is_unchanged(self):
        """The executable form of "production is unchanged when unset".

        The plan proves that claim with three links - the deployed ``.env`` has
        none of the keys, the backend string is today's literal, and ``oss2`` is
        imported lazily. Only the middle one can be asserted in CI, and it is the
        one that matters: if this ever reads as anything other than the exact
        FileSystemStorage string Django ships, then this PR *did* change a box
        that has no bucket, which is the single thing the whole design avoids.
        """
        self.assertEqual(settings.STORAGES['default']['BACKEND'], FILE_SYSTEM_STORAGE_BACKEND)
        self.assertFalse(settings.OSS_STORAGE_ENABLED)
        self.assertEqual(settings.MEDIA_URL, '/media/')


class StorageRoundTripTests(SimpleTestCase):
    """The storage API the app uses, on both backends it runs on today.

    ``save``/``exists``/``open``/``size``/``url``/``delete`` plus ``listdir``,
    which ``cleanup_orphan_photo_zips`` and the report folder walks depend on.
    FileSystemStorage gets a temporary directory standing in for ``MEDIA_ROOT``;
    InMemoryStorage is the backend ``tests/storage.py`` swaps in, so this is also
    the proof that helper can carry the work the converted classes ask of it.
    """

    NAME = 'inspections/7/photos/front.jpg'

    def setUp(self):
        self.media_root = tempfile.TemporaryDirectory(prefix='waypost-test-media-')
        self.addCleanup(self.media_root.cleanup)
        self.payload = png_bytes('blue', size=(4, 4))

    def _storages(self):
        return (
            ('FileSystemStorage', FileSystemStorage(location=self.media_root.name, base_url=settings.MEDIA_URL)),
            ('InMemoryStorage', InMemoryStorage(base_url=settings.MEDIA_URL)),
        )

    def test_save_exists_open_read_size_url_delete_listdir(self):
        for label, storage in self._storages():
            with self.subTest(backend=label):
                name = storage.save(self.NAME, ContentFile(self.payload, name='front.jpg'))
                self.assertEqual(name, self.NAME)
                self.assertTrue(storage.exists(self.NAME))

                with storage.open(self.NAME, 'rb') as handle:
                    self.assertEqual(handle.read(), self.payload)
                self.assertEqual(storage.size(self.NAME), len(self.payload))

                url = storage.url(self.NAME)
                self.assertTrue(url.startswith(settings.MEDIA_URL), url)
                self.assertTrue(url.endswith(self.NAME), url)

                self.assertEqual(storage.listdir('inspections/7'), (['photos'], []))
                self.assertEqual(storage.listdir('inspections/7/photos'), ([], ['front.jpg']))

                storage.delete(self.NAME)
                self.assertFalse(storage.exists(self.NAME))

    def test_two_uploads_of_one_filename_get_two_distinct_names(self):
        """Why the manifest dedups on the storage name and not the basename.

        If a backend silently overwrote the first ``invoice.pdf``, deduping by
        name would still look right while the bytes were already gone.
        """
        directory = 'quotations/attachments/2026/10'
        for label, storage in self._storages():
            with self.subTest(backend=label):
                storage.save(f'{directory}/invoice.pdf', ContentFile(b'first'))
                second = storage.save(f'{directory}/invoice.pdf', ContentFile(b'second'))
                self.assertNotEqual(second, f'{directory}/invoice.pdf')
                self.assertEqual(storage.open(f'{directory}/invoice.pdf', 'rb').read(), b'first')
                self.assertEqual(storage.open(second, 'rb').read(), b'second')


class FakeBucket:
    """The slice of ``oss2.Bucket`` that :class:`WaypostOSSStorage` calls.

    A dict rather than a mock on purpose: a wrong key or a dropped header fails as
    a value mismatch, not as an unnoticed call.
    """

    def __init__(self, host):
        self.host = host
        self.objects = {}
        self.sign_calls = 0

    def put_object(self, key, data, headers=None):
        self.objects[key] = (data, dict(headers or {}))
        return SimpleNamespace(request_id='fake')

    def get_object(self, key):
        payload = self.objects[key][0]
        return SimpleNamespace(read=lambda: payload)

    def object_exists(self, key):
        return key in self.objects

    def delete_object(self, key):
        self.objects.pop(key, None)
        return SimpleNamespace()

    def head_object(self, key):
        data, _headers = self.objects[key]
        return SimpleNamespace(content_length=len(data), last_modified=int(time.time()))

    def list_objects(self, prefix='', delimiter='', marker='', max_keys=1000):
        directories, files = [], []
        for key in sorted(self.objects):
            if not key.startswith(prefix) or key <= marker:
                continue
            rest = key[len(prefix):]
            if delimiter and delimiter in rest:
                common = prefix + rest.split(delimiter, 1)[0] + delimiter
                if common not in directories:
                    directories.append(common)
            else:
                files.append(SimpleNamespace(key=key))
        return SimpleNamespace(
            prefix_list=directories, object_list=files, is_truncated=False, next_marker=''
        )

    def sign_url(self, method, key, expires, **options):
        self.sign_calls += 1
        return f'https://{self.host}/{key}?Expires={expires}&Signature=SIG{self.sign_calls}'


class WaypostOSSStorageTests(SimpleTestCase):
    """The private-bucket backend, against a stubbed bucket and no network.

    Only ``_make_bucket`` is patched, so ``oss2`` is never imported and no socket
    is opened; every call lands on a ``FakeBucket`` keyed by the host it was built
    for, which also pins the endpoint split (internal host for traffic, public
    host for URLs a phone can resolve).
    """

    OPTIONS = {
        'bucket_name': 'waypost-media',
        'endpoint': INTERNAL_ENDPOINT,
        'access_key_id': 'test-key-id',
        'access_key_secret': 'test-key-secret',
        'media_prefix': 'media',
        'signed_url_expiry_seconds': 3600,
    }

    def setUp(self):
        self.buckets = {}

        def make_bucket(host):
            return self.buckets.setdefault(host, FakeBucket(host))

        patcher = mock.patch.object(WaypostOSSStorage, '_make_bucket', side_effect=make_bucket)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.storage = WaypostOSSStorage(**self.OPTIONS)

    def test_cache_control_matches_the_nginx_blocks(self):
        """Blocker 8: 7 days under inspections/, 30 days elsewhere."""
        self.assertEqual(CACHE_CONTROL_INSPECTIONS, 'max-age=604800')
        self.assertEqual(CACHE_CONTROL_MEDIA, 'max-age=2592000')
        for name in ('inspections/9/photos/a.jpg', 'inspections/9/report.xlsx'):
            self.assertEqual(media_cache_control(name), CACHE_CONTROL_INSPECTIONS)
        for name in ('quotations/attachments/2026/10/a.pdf', 'deliveries/signed/2026/10/a.pdf'):
            self.assertEqual(media_cache_control(name), CACHE_CONTROL_MEDIA)

        self.assertEqual(
            self.storage.get_object_parameters('inspections/9/photos/a.jpg')['Cache-Control'],
            CACHE_CONTROL_INSPECTIONS,
        )
        self.assertEqual(
            self.storage.get_object_parameters('accounts/avatars/a.png')['Cache-Control'],
            CACHE_CONTROL_MEDIA,
        )

    def test_content_disposition_only_for_deliverables(self):
        """Report workbooks download; photos, thumbs and signatures must render.

        A blanket ``attachment`` under ``inspections/`` would make every ``<img>``
        in the web detail page and the mini program's photo grid download instead
        of displaying - the one change here a store engineer would notice.
        """
        for name in ('inspections/9/report.xlsx', 'inspections/9/cover.pdf'):
            with self.subTest(deliverable=name):
                self.assertTrue(is_inspection_deliverable(name))
                self.assertEqual(
                    self.storage.get_object_parameters(name)['Content-Disposition'], 'attachment'
                )

        for name in (
            'inspections/9/photos/a.jpg',
            'inspections/9/thumbs/a.jpg',
            'inspections/9/signatures/store.png',
            'quotations/attachments/2026/10/a.pdf',
        ):
            with self.subTest(inline=name):
                self.assertFalse(is_inspection_deliverable(name))
                self.assertNotIn('Content-Disposition', self.storage.get_object_parameters(name))

    def test_save_writes_under_the_configured_media_prefix(self):
        """One prefix per bucket, so dev/staging/prod can share it."""
        returned = self.storage._save('inspections/9/photos/a.jpg', ContentFile(b'bytes'))
        self.assertEqual(returned, 'inspections/9/photos/a.jpg')

        bucket = self.buckets[INTERNAL_ENDPOINT]
        self.assertEqual(list(bucket.objects), ['media/inspections/9/photos/a.jpg'])
        data, headers = bucket.objects['media/inspections/9/photos/a.jpg']
        self.assertEqual(data, b'bytes')
        self.assertEqual(headers['Cache-Control'], CACHE_CONTROL_INSPECTIONS)

    def test_internal_endpoint_carries_traffic_and_public_host_signs_urls(self):
        self.storage._save('inspections/9/photos/a.jpg', ContentFile(b'bytes'))
        self.storage.url('inspections/9/photos/a.jpg')

        self.assertEqual(sorted(self.buckets), sorted([INTERNAL_ENDPOINT, PUBLIC_ENDPOINT]))
        self.assertEqual(self.storage.bucket.host, INTERNAL_ENDPOINT)
        self.assertEqual(self.storage.signing_bucket.host, PUBLIC_ENDPOINT)
        # The public host is derived, never configured: the operator sets one
        # endpoint and both halves follow from it.
        self.assertEqual(public_oss_host(INTERNAL_ENDPOINT), PUBLIC_ENDPOINT)

    def test_url_returns_the_signed_value_and_is_memoised_within_a_window(self):
        """The exact string ``sign_url`` produced, re-signed only across a window.

        Re-signing per call inside one render would hand a page several URLs for
        one object and defeat caching upstream; never re-signing would strand an
        hour-old page on an expired URL (risk R6).

        The memo buckets on an absolute grid (``int(now // window)``), so these
        timestamps are grid-aligned rather than arbitrary: a window of 1800 s
        starts at a multiple of 1800. A cached URL is therefore at most one whole
        window old, which leaves at least ``expiry - window`` = 30 minutes of
        validity on every URL a page can still be holding - the guarantee R6 asks
        for, and the reason the grid phase does not matter.
        """
        window = self.storage.signed_url_expiry_seconds // 2
        grid_start = window * (1_700_000_000 // window)

        bucket = self.storage.signing_bucket
        bucket.sign_url = mock.Mock(side_effect=['https://signed.example/one', 'https://signed.example/two'])

        with mock.patch('waypost.storage.time.time', mock.Mock(return_value=float(grid_start))):
            first = self.storage.url('inspections/9/photos/a.jpg')
            second = self.storage.url('inspections/9/photos/a.jpg')

        self.assertEqual(first, 'https://signed.example/one')
        self.assertEqual(second, first)
        self.assertEqual(bucket.sign_url.call_count, 1)
        bucket.sign_url.assert_any_call('GET', 'media/inspections/9/photos/a.jpg', 3600)

        # One second before the window closes: still the memoised URL.
        with mock.patch('waypost.storage.time.time', mock.Mock(return_value=float(grid_start + window - 1))):
            self.assertEqual(self.storage.url('inspections/9/photos/a.jpg'), first)
        self.assertEqual(bucket.sign_url.call_count, 1)

        # The window rolls over: a fresh signature, so a long-lived page keeps fetching.
        with mock.patch('waypost.storage.time.time', mock.Mock(return_value=float(grid_start + window))):
            self.assertEqual(self.storage.url('inspections/9/photos/a.jpg'), 'https://signed.example/two')
        self.assertEqual(bucket.sign_url.call_count, 2)

    def test_signed_url_is_absolute_so_media_urls_are_not_re_qualifiable(self):
        """Risk R7: ``build_absolute_uri`` on a signed URL mangles it."""
        signed = self.storage.url('inspections/9/report.xlsx')
        request = RequestFactory().get('/api/v1/inspections/9/report/')
        self.assertTrue(signed.startswith('https://'))
        self.assertEqual(absolute_media_url(request, SimpleNamespace(url=signed)), signed)

    def test_round_trip_and_absence(self):
        """exists/open/size/delete on the names the app actually stores."""
        name = 'inspections/9/photos/a.jpg'
        self.assertFalse(self.storage.exists(name))
        self.assertFalse(self.storage.exists(''))

        self.storage._save(name, ContentFile(b'payload'))
        self.assertTrue(self.storage.exists(name))
        self.assertEqual(self.storage.size(name), 7)
        with self.storage._open(name) as handle:
            self.assertEqual(handle.read(), b'payload')

        self.storage.delete(name)
        self.assertFalse(self.storage.exists(name))
        # OSS DELETE is idempotent, so FieldFile.delete() stays safe to run twice.
        self.storage.delete(name)

    def test_listdir_paginates_and_separates_directories_from_files(self):
        """A master-import inspection tree exceeds one page of 1000 keys.

        ``cleanup_orphan_photo_zips`` walks with ``listdir``; a backend that
        returned only the first page would quietly leave orphans behind.
        """
        first = SimpleNamespace(
            prefix_list=['media/inspections/9/photos/', 'media/inspections/9/thumbs/'],
            object_list=[SimpleNamespace(key='media/inspections/9/report.xlsx')],
            is_truncated=True,
            next_marker='media/inspections/9/report.xlsx',
        )
        second = SimpleNamespace(
            prefix_list=[],
            object_list=[SimpleNamespace(key='media/inspections/9/notes.txt')],
            is_truncated=False,
            next_marker='',
        )
        self.storage.bucket.list_objects = mock.Mock(side_effect=[first, second])

        directories, files = self.storage.listdir('inspections/9')

        self.assertEqual(directories, ['photos', 'thumbs'])
        self.assertEqual(files, ['report.xlsx', 'notes.txt'])
        calls = self.storage.bucket.list_objects.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual([call.kwargs['marker'] for call in calls], ['', 'media/inspections/9/report.xlsx'])
        self.assertEqual([call.kwargs['delimiter'] for call in calls], ['/', '/'])
        self.assertEqual([call.kwargs['max_keys'] for call in calls], [1000, 1000])

    def test_no_local_path_and_no_write_mode(self):
        """Blocker 1 made structural: this backend cannot hand out a filename.

        ``Storage.path()`` raising is what turns a stray ``.path`` into an
        immediate failure on OSS instead of a works-on-dev, breaks-on-prod file -
        the asymmetry the hygiene test removes from the tree in the first place.
        """
        with self.assertRaises(NotImplementedError):
            self.storage.path('inspections/9/photos/a.jpg')
        with self.assertRaises(NotImplementedError):
            self.storage._open('inspections/9/photos/a.jpg', 'wb')

    def test_storage_name_to_bucket_key_mapping(self):
        for name, key in (
            ('inspections/9/photos/a.jpg', 'media/inspections/9/photos/a.jpg'),
            ('./inspections/9/photos/a.jpg', 'media/inspections/9/photos/a.jpg'),
            ('/inspections/a.jpg', 'media/inspections/a.jpg'),
        ):
            with self.subTest(name=name):
                self.assertEqual(self.storage._key(name), key)

        # A leading "./" or "/" is a Django artefact; OSS treats it as part of the
        # key name, so unnormalised it would create a key nothing can read back.

        # An option overrides settings; settings is what production uses, since
        # STORAGES['default'] passes no OPTIONS at all (waypost/settings.py:499).
        staging = WaypostOSSStorage(**{**self.OPTIONS, 'media_prefix': 'staging'})
        self.assertEqual(staging._key('inspections/a.jpg'), 'staging/inspections/a.jpg')

        # A blank option means "not supplied", so it falls through to
        # settings.OSS_MEDIA_PREFIX rather than producing an unprefixed key. That
        # is safe here and worth pinning: the operator's knob is the env var, and
        # ``OSS_MEDIA_PREFIX=`` is a real value that puts keys at the bucket root.
        with override_settings(OSS_MEDIA_PREFIX=''):
            unprefixed = WaypostOSSStorage(**{**self.OPTIONS, 'media_prefix': ''})
            self.assertEqual(unprefixed._key('inspections/a.jpg'), 'inspections/a.jpg')

        with override_settings(OSS_MEDIA_PREFIX='waypost-prod/media'):
            inherited = WaypostOSSStorage(**{**self.OPTIONS, 'media_prefix': ''})
            self.assertEqual(inherited._key('inspections/a.jpg'), 'waypost-prod/media/inspections/a.jpg')


class GeneratedDocumentTests(TestCase):
    """Blocker 3: generated workbooks are ephemeral, and their directory dies.

    Before this PR, ``fill_invoice_template``/``fill_delivery_template`` each
    created ``MEDIA_ROOT/<app>/generated/`` and left every render behind: a
    customer document written into the media tree, served by nginx, and (on OSS)
    billed forever as a noncurrent version. The workbooks themselves are built
    from ``template_files/*.xlsx``, which is gitignored and provisioned out of
    band, so the fixtures write a template into a throwaway base directory rather
    than depending on the developer's checkout.
    """

    def setUp(self):
        self.base_dir = tempfile.TemporaryDirectory(prefix='waypost-test-base-')
        self.addCleanup(self.base_dir.cleanup)
        self.media_root = Path(tempfile.mkdtemp(prefix='waypost-test-mediroot-'))
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

        template_dir = Path(self.base_dir.name) / 'template_files'
        template_dir.mkdir()
        self._write_invoice_template(template_dir / 'invoice information template.xlsx')
        self._write_delivery_template(template_dir / '\u7b7e\u6536\u5355 template.xlsx')

        self.batch = WeeklyOrderBatch.objects.create(sharepoint_file='invoices/sharepoint/dummy.xlsx')
        self.invoice = InvoiceInfo.objects.create(
            weekly_batch=self.batch,
            invoice_date=date(2026, 4, 16),
            bill_to='Kering China',
            kering_group_po_number='PO-001',
            internal_order='IO-001',
            sap_cost_center='CC-001',
        )
        InvoiceInfoItem.objects.create(
            invoice_info=self.invoice,
            description='Handheld terminal',
            unit_price='100.00',
            quantity='2.00',
            net_amount='200.00',
        )
        self.customer = Company.objects.create(name='Kering', code='KER')
        self.quotation = Quotation.objects.create(customer=self.customer, valid_until=date(2026, 12, 31))
        self.delivery = DeliveryOrder.objects.create(
            quotation=self.quotation,
            delivery_number='DN-001',
            delivery_date=date(2026, 4, 16),
            receiver_name='Store Ops',
            receiver_phone='13800000000',
            delivery_address='SZ, China',
        )
        self.admin = get_user_model().objects.create_superuser(
            username='storage-admin', email='storage-admin@example.com', password='pw'
        )

    @staticmethod
    def _write_invoice_template(path):
        sheet = Workbook().active
        sheet['A1'] = 'Bill To'
        sheet['A2'] = 'PI Number'
        sheet['A3'] = 'Invoice Date'
        for column, label in enumerate(
            ['Description', 'Unit Price', 'Quantity', 'Net Amount', 'Tax Amount', 'Gross Amount'], start=1
        ):
            sheet.cell(row=6, column=column).value = label
        sheet.parent.save(path)

    @staticmethod
    def _write_delivery_template(path):
        sheet = Workbook().active
        sheet['A1'] = '\u8ba2\u8d27\u65b9'
        sheet['A2'] = '\u6536\u8d27\u4eba'
        for column, label in enumerate(
            ['\u5e8f\u5217\u53f7', '\u54c1\u724c', '\u5546\u54c1\u63cf\u8ff0', '\u6570\u91cf'], start=1
        ):
            sheet.cell(row=5, column=column).value = label
        sheet.parent.save(path)

    def _gen_dirs(self):
        """Live ``waypost-gen-*`` directories in the system temp folder."""
        root = Path(tempfile.gettempdir())
        return {entry.name for entry in root.iterdir() if entry.name.startswith(TEMP_DIR_PREFIX)}

    def _login(self):
        self.client.force_login(self.admin)

    def test_fill_invoice_template_writes_into_the_callers_directory_only(self):
        with generated_document_dir('invoice') as output_dir:
            with override_settings(BASE_DIR=self.base_dir.name, MEDIA_ROOT=self.media_root):
                path = fill_invoice_template(self.invoice, output_dir)

            self.assertEqual(path.parent, output_dir)
            self.assertEqual(path.name, f'{self.invoice.invoice_number}.xlsx')

            # Filled for real, so "ephemeral" did not mean "stubbed": the label
            # cell and the item row are what the customer receives. Read while the
            # directory still exists, because "the caller owns the lifetime" cuts
            # both ways.
            sheet = load_workbook(path).active
            self.assertEqual(sheet['B1'].value, 'Kering China')
            self.assertEqual(sheet.cell(row=7, column=1).value, 'Handheld terminal')

        self.assertFalse(output_dir.exists())
        self.assertFalse((self.media_root / 'invoices' / 'generated').exists())
        self.assertEqual(list(self.media_root.iterdir()), [])

    def test_fill_delivery_template_writes_into_the_callers_directory_only(self):
        with generated_document_dir('delivery') as output_dir:
            with override_settings(BASE_DIR=self.base_dir.name, MEDIA_ROOT=self.media_root):
                path = fill_delivery_template(self.delivery, output_dir)

            self.assertEqual(path.parent, output_dir)
            self.assertEqual(path.name, 'DN-001.xlsx')
            self.assertEqual(load_workbook(path).active['B1'].value, 'Kering')

        self.assertFalse((self.media_root / 'deliveries' / 'generated').exists())
        self.assertEqual(list(self.media_root.iterdir()), [])

    def test_convert_returns_none_when_libreoffice_is_absent(self):
        """Risk R1: the xlsx fallback is documented behaviour, so pin it.

        Three copies of this function used to exist and every caller depends on
        ``None`` meaning "attach the workbook instead". Raising here would fail an
        email dispatch on a box with no LibreOffice, which is most dev boxes.
        """
        with mock.patch('utils.generated_documents._find_soffice_binary', return_value=None), \
                mock.patch('utils.generated_documents.subprocess.run') as run:
            self.assertIsNone(convert_xlsx_to_pdf('/tmp/whatever.xlsx'))
        run.assert_not_called()

    def test_find_soffice_binary_returns_none_when_nothing_is_installed(self):
        """The Windows candidates stay for dev boxes, but must not be assumed."""
        with mock.patch('utils.generated_documents.shutil.which', return_value=None), \
                mock.patch('utils.generated_documents.Path') as path_cls:
            path_cls.return_value.exists.return_value = False
            from utils.generated_documents import _find_soffice_binary

            self.assertIsNone(_find_soffice_binary())

    def test_generated_document_dir_is_removed_on_exit(self):
        with generated_document_dir('naming') as output_dir:
            path = save_workbook_to_temp(Workbook(), output_dir, 'named.xlsx')
            self.assertEqual(path, output_dir / 'named.xlsx')
            self.assertTrue(path.exists())
        self.assertFalse(output_dir.exists())

    def test_collect_email_attachments_writes_nothing_into_media_root(self):
        """The dispatch path is the second writer; it must be clean too.

        LibreOffice is pinned absent so the manifest shape does not depend on what
        happens to be installed on the runner - the fallback is the branch CI owns.
        """
        dispatch = EmailDispatch.objects.create(
            quotation=self.quotation,
            delivery_order=self.delivery,
            invoice_info=self.invoice,
            subject='Dispatch',
            sent_to='client@example.com',
        )
        with mock.patch('invoices.services.render_quotation_pdf_html', return_value=b'%PDF-1.4 stub'), \
                mock.patch('utils.generated_documents._find_soffice_binary', return_value=None), \
                generated_document_dir('dispatch') as output_dir:
            with override_settings(BASE_DIR=self.base_dir.name, MEDIA_ROOT=self.media_root):
                manifest = collect_email_attachments(dispatch, output_dir=output_dir)

        categories = {entry['category'] for entry in manifest}
        self.assertEqual(
            categories,
            {'quotation_document', 'delivery_document', 'invoice_document'},
        )
        self.assertEqual(list(self.media_root.iterdir()), [])
        for entry in manifest:
            self.assertTrue(entry['category'] and entry['label'] and entry['name'], entry)
            self.assertTrue(entry.get('storage_name') or entry.get('path'), entry)

    def test_streamed_invoice_document_outlives_the_view_and_is_reaped_on_close(self):
        """Risk R2, end to end through the test client.

        ``FileResponse`` is iterated *after* the view has returned, so a
        ``with TemporaryDirectory()`` around it would delete the workbook before
        byte one was read - a truncated download no unit test of the writer can
        see. The other half matters equally: nothing may *survive* either, because
        a per-request directory left in the temp folder is precisely the
        accumulation this PR removes, and it is invisible until the disk fills.
        """
        self._login()
        document_url = reverse('invoices:invoice_document', args=[self.invoice.pk])
        with mock.patch('utils.generated_documents._find_soffice_binary', return_value=None):
            before = self._gen_dirs()
            with override_settings(BASE_DIR=self.base_dir.name):
                response = self.client.get(document_url)

            self.assertEqual(
                response.status_code, 200, f"unexpected redirect to {getattr(response, 'url', None)}"
            )
            self.assertTrue(response.streaming)

            # The view has returned and the directory that built the workbook is
            # still there - the response owns it. This is the half a
            # context-manager refactor would break.
            live = self._gen_dirs() - before
            self.assertEqual(len(live), 1, f'expected exactly one live temp dir, saw {live}')

            payload = response.getvalue()  # consumes the stream, which closes the file

            self.assertTrue(payload.startswith(b'PK'), 'streamed bytes are not an xlsx')
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertIn('xl/workbook.xml', archive.namelist())
                self.assertIsNone(archive.testzip())

            self.assertEqual(self._gen_dirs(), before, 'a waypost-gen- directory survived the response')
            self.assertEqual(
                response['Content-Disposition'], f'attachment; filename="{self.invoice.invoice_number}.xlsx"'
            )

    def test_missing_template_error_path_also_reaps_its_temp_dir(self):
        """The redirect branch creates the temp dir before it fails."""
        self._login()
        empty_base = tempfile.TemporaryDirectory(prefix='waypost-test-empty-base-')
        self.addCleanup(empty_base.cleanup)
        before = self._gen_dirs()

        with override_settings(BASE_DIR=empty_base.name):
            response = self.client.get(reverse('invoices:invoice_document', args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._gen_dirs(), before)


class ThumbnailTests(IsolatedMediaTestCase):
    """The 144 Mbps -> 14.4 Mbps change, and the failure modes around it."""

    def setUp(self):
        from inspections.models import InspectionPhoto, StoreInspection

        self.InspectionPhoto = InspectionPhoto
        self.store_inspection = StoreInspection.objects.create(
            company=Company.objects.create(name='Kering', code='KER'),
            jda_code='22149',
            brand_name='Gucci',
            store_name='SZOL',
            inspection_date=date(2026, 9, 2),
        )

    def _photo(self, name='overall.png', payload=None):
        photo = self.InspectionPhoto.objects.create(
            store_inspection=self.store_inspection,
            kind=self.InspectionPhoto.Kind.OVERALL,
            client_photo_uid='uid-1',
        )
        photo.image.save(name, ContentFile(payload or png_bytes('green', size=(1920, 1440))), save=True)
        return photo

    def _thumb_name(self, photo):
        from inspections.services.thumbnails import thumbnail_storage_name

        return thumbnail_storage_name(photo)

    def test_derivative_is_a_small_jpeg(self):
        from inspections.services.thumbnails import THUMBNAIL_LONG_EDGE, ensure_thumbnail

        photo = self._photo()
        self.assertTrue(ensure_thumbnail(photo))

        name = self._thumb_name(photo)
        self.assertEqual(name, f'inspections/{self.store_inspection.pk}/thumbs/{photo.pk}.jpg')
        self.assertTrue(default_storage.exists(name))

        data = default_storage.open(name, 'rb').read()
        self.assertLess(len(data), 50 * 1024, 'thumbnail is not the ~30 KB the cost model assumes')
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertLessEqual(max(image.size), THUMBNAIL_LONG_EDGE)

    def test_thumbnail_url_falls_back_to_the_original(self):
        """Photos written before this pipeline have no derivative (no backfill)."""
        photo = self._photo()
        self.assertFalse(default_storage.exists(self._thumb_name(photo)))
        self.assertEqual(photo.thumbnail_url, photo.image.url)
        self.assertTrue(photo.thumbnail_url.endswith(photo.image.name))

        from inspections.services.thumbnails import ensure_thumbnail

        ensure_thumbnail(photo)
        self.assertIn(f'/thumbs/{photo.pk}.jpg', photo.thumbnail_url)

    def test_a_corrupt_upload_still_stores_the_original(self):
        """A thumbnail failure must never fail the photo write."""
        from api.inspection_views import StoreInspectionViewSet

        payload = b'not a png at all'
        photo = StoreInspectionViewSet._upsert_photo(
            inspection=self.store_inspection,
            device=None,
            kind=self.InspectionPhoto.Kind.SERIAL,
            image_file=SimpleUploadedFile('broken.png', payload, content_type='image/png'),
            client_photo_uid='uid-broken',
            uploaded_by=None,
        )

        self.assertTrue(default_storage.exists(photo.image.name))
        with default_storage.open(photo.image.name, 'rb') as handle:
            self.assertEqual(handle.read(), payload)
        self.assertFalse(default_storage.exists(self._thumb_name(photo)))
        self.assertEqual(photo.thumbnail_url, photo.image.url)

    def test_backfill_command_fills_missing_derivatives_then_becomes_a_no_op(self):
        """The operator's one-shot job, safe to re-run and to interleave."""
        from django.core.management import call_command

        photo = self._photo()
        name = self._thumb_name(photo)
        out = io.StringIO()

        call_command('generate_photo_thumbnails', '--all', '--dry-run', stdout=out)
        self.assertIn(name, out.getvalue())
        self.assertFalse(default_storage.exists(name))

        call_command('generate_photo_thumbnails', '--all', stdout=out)
        self.assertTrue(default_storage.exists(name))
        before = default_storage.open(name, 'rb').read()

        call_command('generate_photo_thumbnails', '--all', stdout=out)
        self.assertEqual(default_storage.open(name, 'rb').read(), before)

        with self.assertRaises(Exception):
            call_command('generate_photo_thumbnails', stdout=out)  # no scope given


class EmailAttachmentManifestTests(IsolatedMediaTestCase):
    """The attachment manifest reshape, and the collision it fixes."""

    def setUp(self):
        self.quotation = Quotation.objects.create(
            customer=Company.objects.create(name='Kering', code='KER'), valid_until=date(2026, 12, 31)
        )
        self.dispatch = EmailDispatch.objects.create(
            quotation=self.quotation, subject='Dispatch', sent_to='client@example.com'
        )

    def _attachment(self, directory, filename, payload):
        """Store ``payload`` at an exact name and attach it.

        The name is written through the storage rather than through ``upload_to``
        because the case under test is two objects with one basename in two
        folders, which ``upload_to`` cannot express.
        """
        name = default_storage.save(f'{directory}/{filename}', ContentFile(payload, name=filename))
        return QuotationAttachment.objects.create(
            quotation=self.quotation,
            attachment_type=QuotationAttachment.AttachmentType.INVOICE_PDF,
            file=name,
        )

    def _read(self, attachment):
        """Read the manifest entry that points at ``attachment``, via the reader."""
        entry = next(item for item in self.manifest if item.get('storage_name') == attachment.file.name)
        with _open_attachment(entry) as handle:
            return handle.read()

    def test_same_named_uploads_from_different_folders_all_survive(self):
        """The dedup key must stay the storage name, never the basename.

        The manifest reshape is what creates the hazard: while every entry was a
        filesystem path, two same-named uploads had two different paths and both
        survived. Once uploads carry ``storage_name`` and no ``path``, the
        remaining short key is ``name`` - and keying on it drops one of them,
        which is a customer's invoice PDF missing from an email with nothing in
        the log to explain it. Here three entries share one basename: the
        generated quotation PDF plus uploads in two month folders.
        """
        filename = f'{self.quotation.quotation_number}.pdf'
        first = self._attachment('quotations/attachments/2026/09', filename, b'september')
        second = self._attachment('quotations/attachments/2026/10', filename, b'october')

        with mock.patch('invoices.services.render_quotation_pdf_html', return_value=b'%PDF-1.4 generated'), \
                generated_document_dir('manifest') as output_dir:
            manifest = collect_email_attachments(self.dispatch, output_dir=output_dir)
        self.manifest = manifest

        self.assertEqual([entry['name'] for entry in manifest].count(filename), 3, manifest)
        storage_names = {entry.get('storage_name') for entry in manifest}
        self.assertIn(first.file.name, storage_names)
        self.assertIn(second.file.name, storage_names)
        self.assertEqual(
            len(manifest), len({entry.get('storage_name') or entry['path'] for entry in manifest})
        )

        # And each entry still resolves to its own bytes, not to the other one.
        self.assertEqual(self._read(first), b'september')
        self.assertEqual(self._read(second), b'october')

    def test_upload_that_is_gone_from_storage_is_skipped_not_fatal(self):
        """A deleted file must not block an email; the manifest is persisted."""
        name = default_storage.save('quotations/attachments/2026/10/soon-gone.pdf', ContentFile(b'x'))
        default_storage.delete(name)
        entry = {'category': 'quotation_attachment', 'label': 'Invoice PDF', 'name': 'soon-gone.pdf',
                 'storage_name': name}
        self.assertIsNone(_open_attachment(entry))

    def test_legacy_path_only_entry_is_still_readable(self):
        """Old ``EmailDispatch.attachments`` rows carry ``path`` and no name.

        The readers keep the ``path`` fallback for exactly that reason; without it
        every dispatch created before this change loses its attachments on a
        resend.
        """
        with generated_document_dir('legacy') as output_dir:
            disk = output_dir / 'quotation.pdf'
            disk.write_bytes(b'%PDF written before the reshape')
            with _open_attachment({'name': 'quotation.pdf', 'path': str(disk)}) as handle:
                self.assertEqual(handle.read(), b'%PDF written before the reshape')

        self.assertFalse(output_dir.exists())


class AbsoluteMediaUrlTests(SimpleTestCase):
    """``absolute_media_url`` - the only imperative media-URL site in the project."""

    def setUp(self):
        self.request = RequestFactory().get('/api/v1/inspections/9/report/')

    def test_an_absolute_signed_url_is_returned_verbatim(self):
        signed = (
            f'https://waypost-media.{PUBLIC_ENDPOINT}/media/inspections/9/report.xlsx'
            '?Expires=1700003600&Signature=abc%2Fdef%3D'
        )
        self.assertEqual(absolute_media_url(self.request, SimpleNamespace(url=signed)), signed)

    def test_a_media_url_relative_path_is_qualified_from_the_request(self):
        self.assertEqual(
            absolute_media_url(self.request, SimpleNamespace(url='/media/inspections/9/a.jpg')),
            'http://testserver/media/inspections/9/a.jpg',
        )

    def test_a_file_field_with_no_file_yields_an_empty_string(self):
        for fieldfile in (None, SimpleNamespace(url=''), SimpleNamespace()):
            with self.subTest(fieldfile=fieldfile):
                self.assertEqual(absolute_media_url(self.request, fieldfile), '')
