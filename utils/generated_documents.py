"""Ephemeral generated documents: one LibreOffice path, no writes to storage.

The invoice and delivery workbooks were built from ``template_files/*.xlsx``,
consumed once (by ``FileResponse`` or as an SMTP attachment) and left behind in
``MEDIA_ROOT/<app>/generated/`` forever. Nothing links those files - not one of
the ``{{ x.url }}`` template sites points at a ``*/generated/`` path - so they
are pure accumulation, and writing them to an object-storage backend would cost
a PUT plus a GET per email and, under bucket versioning, a permanent
noncurrent-version leak. They are built in a temporary directory, consumed, and
discarded instead.

Three copies of ``_find_soffice_binary``/``convert_xlsx_to_pdf`` existed (one
per app); this is the only one. The argument vector, the ``timeout=120``, the
``DEVNULL`` pipes and ``return None`` on *any* failure are all load-bearing:
callers fall back to attaching the xlsx, and the fallback is what keeps a
missing or intermittently-failing LibreOffice from failing a dispatch.
"""
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.core.files import File

LOGGER = logging.getLogger(__name__)

# Prefix for every directory this module creates, so an operator can reap
# strays with one tmpfiles rule (see docs/OSS_STORAGE.md, "Ephemeral documents").
TEMP_DIR_PREFIX = 'waypost-gen-'


def _find_soffice_binary():
    """Return the LibreOffice CLI path, or None when it is not installed."""
    candidates = [
        shutil.which('soffice'),
        shutil.which('soffice.exe'),
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)

    return None


def convert_xlsx_to_pdf(xlsx_path):
    """Convert ``xlsx_path`` to PDF in its own directory; return the path or None.

    Returns None whenever the PDF was not produced - no LibreOffice, a non-zero
    exit, a timeout, or the intermittent single-instance failure. Callers must
    treat None as "attach the workbook instead", which is why nothing raises.
    """
    soffice = _find_soffice_binary()
    if not soffice:
        return None

    xlsx_path = Path(xlsx_path)
    outdir = str(xlsx_path.parent)

    try:
        subprocess.run(
            [
                soffice,
                '--headless',
                '--convert-to',
                'pdf',
                '--outdir',
                outdir,
                str(xlsx_path),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except Exception:
        return None

    pdf_path = xlsx_path.with_suffix('.pdf')
    return pdf_path if pdf_path.exists() else None


@contextmanager
def generated_document_dir(prefix='document') -> Iterator[Path]:
    """Yield an empty directory for generated documents, removed on exit.

    The caller owns the lifetime: ``collect_email_attachments`` needs the files
    to survive until the message has been built, so handing the directory to a
    per-function ``with`` block would delete them under the reader.
    """
    with tempfile.TemporaryDirectory(prefix=f'{TEMP_DIR_PREFIX}{prefix}-') as tmp:
        yield Path(tmp)


def save_workbook_to_temp(workbook, output_dir, filename):
    """Save an openpyxl workbook into ``output_dir`` and return its path."""
    target = Path(output_dir) / filename
    workbook.save(target)
    return target


class _OwnedFile(File):
    """A readable file that tears down ``holder`` (a TemporaryDirectory) on close.

    ``FileResponse`` streams lazily and registers ``file.close`` as a response
    resource closer, which is the only hook Django offers: a
    ``with TemporaryDirectory()`` inside the view would delete the workbook
    before the first byte is read, and Django's ``HttpResponse`` has no
    ``background=`` callback argument. Cleanup runs after the body has been sent
    (or never, if the worker dies - hence the tmpfiles rule in the docs).
    """

    def __init__(self, handle, holder):
        self._holder = holder
        super().__init__(handle, name=getattr(handle, 'name', None))

    def close(self):
        holder, self._holder = self._holder, None
        try:
            super().close()
        finally:
            if holder is not None:
                try:
                    holder.cleanup()
                except Exception:
                    # A failed teardown must not turn a served download into a 500;
                    # the directory is reaped by the tmpfiles rule instead.
                    LOGGER.warning('Could not remove temporary document directory %s', holder, exc_info=True)


def open_generated_document(path, holder):
    """Open ``path`` for a response, tied to ``holder``'s cleanup.

    ``holder`` must be the ``TemporaryDirectory`` that owns ``path``; keeping the
    pair together at the call site is what makes the file's lifetime visible.
    """
    return _OwnedFile(open(path, 'rb'), holder)
