"""Service tests: KB source-path resolution and document loading (PDF/DOCX/TXT).

Ported from helpdesk/backend/tests/test_kb_source_paths.py (path semantics) and
the loader behaviour. PDF/DOCX fixtures are generated in-test with PyMuPDF /
python-docx; OCR is stubbed so no tesseract binary is needed.
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from hongwen.services.kb import loader, paths


class _KbRootMixin:
    def setUp(self):
        super().setUp()
        self.kb_root = tempfile.mkdtemp(prefix='hongwen-test-kb-')
        self.private_root = tempfile.mkdtemp(prefix='hongwen-test-private-')
        ov = override_settings(HONGWEN_KB_SOURCES_DIR=self.kb_root, HONGWEN_PRIVATE_MEDIA_ROOT=self.private_root)
        ov.enable()
        self.addCleanup(ov.disable)


class StoredPathTests(_KbRootMixin, TestCase):
    def test_to_stored_path_accepts_every_spelling(self):
        cases = {
            'manual.pdf': 'manual.pdf',
            'kb_sources/manual.pdf': 'manual.pdf',
            '/srv/app/kb_sources/manual.pdf': 'manual.pdf',
            'C:\\helpdesk\\kb_sources\\manual.pdf': 'manual.pdf',
            '/absolute/elsewhere/manual.pdf': 'manual.pdf',
            '': '',
        }
        for given, expected in cases.items():
            self.assertEqual(paths.to_stored_path(given), expected, given)

    def test_sources_root_follows_settings(self):
        self.assertEqual(paths.sources_root(), os.path.abspath(self.kb_root))

    def test_resolution_finds_file_under_root(self):
        with open(os.path.join(self.kb_root, 'manual.pdf'), 'wb') as fh:
            fh.write(b'%PDF- fake')
        resolved = paths.resolve_stored_path('manual.pdf')
        self.assertTrue(os.path.exists(resolved))
        self.assertTrue(paths.stored_path_exists('manual.pdf'))

    def test_legacy_absolute_path_finds_moved_file(self):
        with open(os.path.join(self.kb_root, 'manual.pdf'), 'wb') as fh:
            fh.write(b'%PDF- fake')
        legacy = 'C:\\old\\checkout\\kb_sources\\manual.pdf'
        self.assertTrue(paths.stored_path_exists(legacy))

    def test_missing_file_resolves_into_root_but_reported_missing(self):
        resolved = paths.resolve_stored_path('gone.pdf')
        self.assertEqual(resolved, os.path.join(self.kb_root, 'gone.pdf'))
        self.assertFalse(paths.stored_path_exists('gone.pdf'))
        self.assertEqual(paths.missing_stored_paths(['gone.pdf']), ['gone.pdf'])

    def test_empty_value_never_resolves_to_root(self):
        self.assertEqual(paths.resolve_stored_path(''), '')


class LoaderTests(_KbRootMixin, TestCase):
    def test_detect_source_type(self):
        self.assertEqual(loader.detect_source_type('a.pdf'), 'pdf')
        self.assertEqual(loader.detect_source_type('a.docx'), 'docx')
        self.assertEqual(loader.detect_source_type('a.doc'), 'docx')
        self.assertEqual(loader.detect_source_type('a.txt'), 'txt')
        with self.assertRaises(ValueError):
            loader.detect_source_type('a.xyz')

    def test_load_txt(self):
        path = os.path.join(self.kb_root, 'note.txt')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('第一行\n第二行')
        content_hash, segments = loader.load_knowledge_base_document(path)
        self.assertEqual(len(content_hash), 64)
        self.assertEqual(segments[0]['page_or_section'], 'full')
        self.assertIn('第一行', segments[0]['text'])

    def test_load_pdf_extracts_text_and_images(self):
        import fitz

        path = os.path.join(self.kb_root, 'guide.pdf')
        doc = fitz.open()
        page = doc.new_page()
        # ASCII text: fitz's built-in helv font has no CJK glyphs, so Chinese
        # inserted here would not extract. The loader extracts whatever is present.
        page.insert_text((72, 72), 'Router red light: check the power adapter first')
        # Embed a small image so the image-extraction branch runs.
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buf, format='PNG')
        page.insert_image(fitz.Rect(72, 100, 120, 148), stream=buf.getvalue())
        doc.save(path)
        doc.close()

        with mock.patch('hongwen.services.utils.ocr.ocr_image_bytes', return_value=''):
            content_hash, segments = loader.load_knowledge_base_document(path)
        self.assertEqual(len(content_hash), 64)
        self.assertEqual(segments[0]['page_or_section'], 'page_1')
        self.assertIn('power adapter', segments[0]['text'])
        self.assertTrue(segments[0]['images'])
        # Extracted image lives in private storage under kb_assets/.
        self.assertTrue(segments[0]['images'][0].startswith('kb_assets/'))

    def test_load_docx_extracts_paragraphs(self):
        import docx

        path = os.path.join(self.kb_root, 'guide.docx')
        d = docx.Document()
        d.add_paragraph('收银打印机离线处理步骤')
        d.save(path)

        with mock.patch('hongwen.services.utils.ocr.ocr_image_bytes', return_value=''):
            content_hash, segments = loader.load_knowledge_base_document(path)
        self.assertEqual(len(content_hash), 64)
        self.assertEqual(segments[0]['page_or_section'], 'docx')
        self.assertIn('收银打印机', segments[0]['text'])

    def test_sha256_file_is_stable(self):
        path = os.path.join(self.kb_root, 'note.txt')
        with open(path, 'wb') as fh:
            fh.write(b'hello')
        self.assertEqual(loader.sha256_file(path), loader.sha256_file(path))