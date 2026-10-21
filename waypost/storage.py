"""Project infrastructure: the gated object-storage backend.

Sits beside :mod:`waypost.health` and :mod:`waypost.runtime_setup` - the repo's
convention for non-app project plumbing. Two halves live here:

1. **The gate** (:func:`resolve_default_storage_backend`), a pure function of an
   environment mapping with no ``settings`` import, so the decision of which
   backend ``STORAGES['default']`` gets is unit-testable without reloading
   settings and cannot drift from it.
2. **The backend** (:class:`WaypostOSSStorage`), which is the only place Waypost's
   media semantics live: bucket-side ``Cache-Control`` / ``Content-Disposition``,
   signed URLs for a private bucket, and a storage-name -> key mapping.

``oss2`` is imported *inside* the backend, never at module scope. Two reasons:
Django imports this module for the gate on every settings load, and a broken or
absent ``oss2`` install must not break a box that is using FileSystemStorage.

Why the backend is written against ``oss2`` directly: ``django-storages`` has no
Aliyun OSS backend (1.14.6, the latest release, ships apache_libcloud, azure,
dropbox, ftp, gcloud, s3 and sftp only), so the subclass the plan assumed has no
upstream class to subclass.
"""
import io
import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.storage import Storage
from django.utils.functional import cached_property

FILE_SYSTEM_STORAGE_BACKEND = 'django.core.files.storage.FileSystemStorage'
OSS_STORAGE_BACKEND = 'waypost.storage.WaypostOSSStorage'

#: The four keys that together mean "this box is on OSS". Any other combination
#: is either "not configured" (none set) or a half-finished edit (fail fast).
OSS_REQUIRED_ENV_KEYS = (
    'OSS_ENDPOINT',
    'OSS_BUCKET_NAME',
    'OSS_ACCESS_KEY_ID',
    'OSS_ACCESS_KEY_SECRET',
)

# Cache-Control values transcribed from docker/nginx/waypost.conf, where the
# /media/inspections/ block (now :115) says ``expires 7d`` and the /media/ block
# (now :121) says ``expires 30d``. Keeping the numbers identical means an object's
# browser cache lifetime does not change at the cutover.
CACHE_CONTROL_INSPECTIONS = 'max-age=604800'
CACHE_CONTROL_MEDIA = 'max-age=2592000'

INSPECTIONS_ROOT = 'inspections'

# Sub-folders of an inspection folder whose contents must render inline: a
# Content-Disposition of ``attachment`` on them would make an <img> or a photo
# grid download instead of display. Everything else under inspections/ is a
# deliverable (the report workbook), which nginx forces to download at :116.
INLINE_MEDIA_FOLDERS = ('photos', 'signatures', 'thumbs')

# Upper bound on the signed-URL memo, so a long-lived worker cannot grow it
# without limit; exceeding it drops the whole memo rather than evicting
# selectively, because every entry is cheap to re-sign.
SIGNED_URL_CACHE_MAX = 4096


def resolve_default_storage_backend(env) -> str:
    """Return the import path ``STORAGES['default']['BACKEND']`` should hold.

    ``env`` is any mapping (``os.environ`` in settings, a dict in tests). All
    four required keys must be present and non-empty; with anything else the
    answer is today's literal FileSystemStorage path.
    """
    if all(str(env.get(key) or '').strip() for key in OSS_REQUIRED_ENV_KEYS):
        return OSS_STORAGE_BACKEND
    return FILE_SYSTEM_STORAGE_BACKEND


def oss_endpoint_host(endpoint):
    """Bare lowercase host of an endpoint value, in any accepted form."""
    return str(endpoint or '').split('://', 1)[-1].strip('/').lower()


def public_oss_host(endpoint):
    """The host a browser or phone can actually reach for a given endpoint.

    Aliyun's internal endpoint (``oss-cn-heyuan-internal.aliyuncs.com``) is the
    public host with ``-internal`` inserted. It bills no public egress and is
    only reachable from inside the region, so uploads should use it - but a
    signed URL handed to the mini program cannot. OSS V1 signatures cover the
    bucket and key, not the host, so rewriting the host of a signed URL keeps it
    valid, which is what lets one bucket serve free uploads and paid-but-bounded
    downloads from the same credentials.
    """
    host = oss_endpoint_host(endpoint)
    return host.replace('-internal.', '.', 1) if '-internal.' in host else host


def is_inspection_media(name):
    """True for anything under the inspections/ tree."""
    return str(name or '').lstrip('./').split('/', 1)[0] == INSPECTIONS_ROOT


def is_inspection_deliverable(name):
    """True for an inspection file that must download rather than render."""
    if not is_inspection_media(name):
        return False
    segments = [part for part in str(name).lstrip('./').split('/') if part][1:]
    return not any(segment in INLINE_MEDIA_FOLDERS for segment in segments)


def media_cache_control(name):
    """Return the ``Cache-Control`` header value for a stored media name."""
    return CACHE_CONTROL_INSPECTIONS if is_inspection_media(name) else CACHE_CONTROL_MEDIA


class WaypostOSSStorage(Storage):
    """Private-bucket Aliyun OSS storage for all media.

    Instantiated only when ``settings.OSS_STORAGE_ENABLED`` is true, i.e. only
    after an operator has filled in all four ``OSS_*`` keys.

    Deliberate omissions: no ``path()`` (the base class raises, which is the
    point - filesystem access is what this PR removes) and no public-read
    fallback in :meth:`url` (a private bucket is the premise of this class).
    """

    def __init__(self, **options):
        self._signed_urls = {}
        # Django passes STORAGES['default']['OPTIONS'] as kwargs, so each key
        # below overrides its settings value; that is also how a test can build a
        # storage without a settings reload.
        self.bucket_name = options.get('bucket_name') or settings.OSS_BUCKET_NAME
        self.endpoint = options.get('endpoint') or settings.OSS_ENDPOINT
        self.access_key_id = options.get('access_key_id') or settings.OSS_ACCESS_KEY_ID
        self.access_key_secret = options.get('access_key_secret') or settings.OSS_ACCESS_KEY_SECRET
        self.media_prefix = (options.get('media_prefix') or settings.OSS_MEDIA_PREFIX).strip('/')
        self.signed_url_expiry_seconds = int(
            options.get('signed_url_expiry_seconds') or settings.OSS_SIGNED_URL_EXPIRY_SECONDS
        )

    # -- wiring -------------------------------------------------------------

    @staticmethod
    def _oss2():
        """Import ``oss2`` on first use, so an unused install can never break boot."""
        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - depends on the box
            raise ImproperlyConfigured(
                'OSS storage is enabled but the oss2 package is not installed. '
                'Install the version pinned in requirements.txt, or unset the '
                'OSS_* keys to go back to FileSystemStorage.'
            ) from exc
        return oss2

    def _make_bucket(self, host):
        oss2 = self._oss2()
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        return oss2.Bucket(auth, f'https://{host}', self.bucket_name)

    @cached_property
    def bucket(self):
        """Handle on the endpoint as configured - the internal one, if set."""
        return self._make_bucket(oss_endpoint_host(self.endpoint))

    @cached_property
    def signing_bucket(self):
        """Handle on the public host: only used to build URLs, never for traffic."""
        return self._make_bucket(public_oss_host(self.endpoint))

    def _key(self, name):
        """Map a Django storage name to a bucket key (``<prefix>/<name>``)."""
        name = str(name).lstrip('./')
        return f'{self.media_prefix}/{name}' if self.media_prefix else name

    # -- storage API --------------------------------------------------------

    def get_object_parameters(self, name):
        """OSS headers for a new object, keyed on its storage name.

        Real HTTP header names, because this dict is handed straight to
        ``put_object(headers=...)``.
        """
        params = {'Cache-Control': media_cache_control(name)}
        if is_inspection_deliverable(name):
            params['Content-Disposition'] = 'attachment'
        return params

    def _save(self, name, content):
        # Buffered rather than streamed: uploads are already capped at
        # MAX_UPLOAD_SIZE (5 MB) by the API, and ``put_object`` on a file-like
        # body sends chunked transfer-encoding, which is the one path here with
        # no local way to prove it.
        seek = getattr(content, 'seek', None)
        if seek is not None:
            seek(0)
        self.bucket.put_object(self._key(name), content.read(), headers=self.get_object_parameters(name))
        return name

    def _open(self, name, mode='rb'):
        if 'r' not in mode:
            raise NotImplementedError('OSS storage supports reading only.')
        data = self.bucket.get_object(self._key(name)).read()
        return File(io.BytesIO(data), name=name)

    def exists(self, name):
        if not name:
            return False
        return self.bucket.object_exists(self._key(name))

    def delete(self, name):
        # OSS DELETE is idempotent, so a missing object is not an error and
        # FieldFile.delete() stays safe to call twice.
        if name:
            self.bucket.delete_object(self._key(name))

    def size(self, name):
        return self.bucket.head_object(self._key(name)).content_length

    def get_modified_time(self, name):
        head = self.bucket.head_object(self._key(name))
        return datetime.fromtimestamp(head.last_modified, tz=timezone.utc)

    def listdir(self, path):
        """Return ``(directories, files)`` relative to ``path``, like FileSystemStorage.

        ``delimiter='/'`` is what makes this a directory listing rather than a key
        dump: OSS collapses anything deeper into ``prefix_list``. Paginated,
        because an inspection tree from a master import exceeds one page.
        """
        prefix = self._key(path).rstrip('/') + '/'
        directories, files = [], []
        marker = ''
        while True:
            result = self.bucket.list_objects(prefix=prefix, delimiter='/', marker=marker, max_keys=1000)
            for common_prefix in result.prefix_list or []:
                leaf = common_prefix[len(prefix):].strip('/')
                if leaf:
                    directories.append(leaf)
            for obj in result.object_list or []:
                leaf = obj.key[len(prefix):]
                if leaf:
                    files.append(leaf)
            if not result.is_truncated:
                return directories, files
            marker = result.next_marker

    def url(self, name):
        """Signed GET URL for a private bucket, memoised per name per window.

        ``oss2``'s own signing call is CPU-only (no network), so the memo is not
        about load - it is about *identity*: re-signing on every call within one
        render would hand the same page several different URLs for one object and
        defeat any caching upstream. Re-signing once the window passes is what
        keeps an hour-old page from suddenly holding an expired URL.
        """
        window = max(1, self.signed_url_expiry_seconds // 2)
        cache_key = (str(name), int(time.time() // window))
        signed = self._signed_urls.get(cache_key)
        if signed is None:
            signed = self.signing_bucket.sign_url('GET', self._key(name), self.signed_url_expiry_seconds)
            if len(self._signed_urls) >= SIGNED_URL_CACHE_MAX:
                self._signed_urls.clear()
            self._signed_urls[cache_key] = signed
        return signed
