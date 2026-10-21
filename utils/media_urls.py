"""Absolute media URLs that survive an object-storage backend.

Cross-app helper (the ``utils`` convention, as with ``csv_import`` /
``import_rollback``): a request plus a ``FieldFile`` is a thing several apps need
and no app owns.

The defect this exists for: ``request.build_absolute_uri(field.url)``. That is
harmless while ``storage.url()`` returns a root-relative ``/media/...`` path, and
silently wrong the moment a backend returns an absolute URL - ``build_absolute_uri``
glues its argument onto the site host, producing
``https://ams.istore-tech.cn/https://bucket.oss-.../media/x.jpg``. Signed OSS URLs
are always absolute, so every imperative media-URL site must go through here.
"""


def absolute_media_url(request, fieldfile):
    """Return an absolute URL for ``fieldfile``, or '' when it has no file.

    A URL that already carries a scheme is returned unchanged - it is the storage
    backend's own answer, including any signature, and re-qualifying it breaks it.
    """
    url = getattr(fieldfile, 'url', '') if fieldfile else ''
    if not url:
        return ''
    if url.startswith(('http://', 'https://')):
        return url
    return request.build_absolute_uri(url)
