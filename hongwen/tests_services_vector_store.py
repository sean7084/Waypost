"""Service tests: KB vector retrieval (dual backend) and ingestion on the ORM.

The SQLite brute-force cosine path runs everywhere; the PostgreSQL raw-SQL path
is gated on connection.vendor. Embeddings are stubbed with the hash-based
stub_vector so ranking is deterministic and no provider key is needed.
"""
import os
import tempfile
from unittest import mock, skipUnless

from django.db import connection
from django.test import TestCase, override_settings

from hongwen.models import Customer, KBChunk, KBDocument
from hongwen.services.kb import ingest, vector_store
from hongwen.services_testing import stub_vector


def _fake_embed_text(*, model, texts, **_):
    return [stub_vector(t) for t in texts]


class VectorHelpersTests(TestCase):
    def test_coerce_dimension_pads(self):
        vec = vector_store._coerce_embedding_dimension([0.5, 0.5])
        self.assertEqual(len(vec), 1536)
        self.assertEqual(vec[0], 0.5)
        self.assertEqual(vec[-1], 0.0)

    def test_coerce_dimension_truncates(self):
        vec = vector_store._coerce_embedding_dimension([1.0] * 2000)
        self.assertEqual(len(vec), 1536)

    def test_to_vector_literal(self):
        self.assertEqual(vector_store._to_vector_literal([0.1, 0.2]), '[0.1,0.2]')

    def test_cosine_similarity(self):
        self.assertAlmostEqual(vector_store.cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(vector_store.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(vector_store.cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_empty_query_returns_empty(self):
        self.assertEqual(vector_store.search_kb(query='   '), [])


class _KbDataMixin:
    def setUp(self):
        super().setUp()
        self.customer_a = Customer.objects.create(name='Acme')
        self.customer_b = Customer.objects.create(name='Globex')
        self.doc_global = KBDocument.objects.create(path='global.pdf', source_type='pdf', content_hash='h0', customer=None)
        self.doc_a = KBDocument.objects.create(path='acme.pdf', source_type='pdf', content_hash='ha', customer=self.customer_a)
        self.doc_b = KBDocument.objects.create(path='globex.pdf', source_type='pdf', content_hash='hb', customer=self.customer_b)
        self._chunk(self.doc_global, 0, 'router power adapter reset', 'page_1')
        self._chunk(self.doc_a, 0, 'acme printer paper jam', 'page_2')
        self._chunk(self.doc_b, 0, 'globex secret sauce recipe', 'page_3')

    def _chunk(self, doc, index, text, page):
        return KBChunk.objects.create(
            document=doc, chunk_index=index, content=text, chunk_type='text',
            metadata_json={'images': []}, embedding=stub_vector(text),
            source_path=getattr(doc, 'path'), page_or_section=page,
        )


class SearchSqliteTests(_KbDataMixin, TestCase):
    def test_identical_text_scores_highest(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='router power adapter reset', customer_id=None, top_k=5)
        self.assertTrue(results)
        self.assertEqual(results[0]['snippet'], 'router power adapter reset')
        self.assertAlmostEqual(results[0]['score'], 1.0, places=5)
        self.assertEqual(results[0]['source'], 'global.pdf')
        self.assertEqual(results[0]['page_or_section'], 'page_1')

    def test_no_customer_sees_only_global(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='printer paper jam', customer_id=None, top_k=10)
        sources = {r['source'] for r in results}
        self.assertEqual(sources, {'global.pdf'})

    def test_customer_sees_own_plus_global_not_others(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='printer paper jam', customer_id=self.customer_a.id, top_k=10)
        sources = {r['source'] for r in results}
        self.assertEqual(sources, {'global.pdf', 'acme.pdf'})
        self.assertNotIn('globex.pdf', sources)

    def test_top_k_limits(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='router', customer_id=self.customer_a.id, top_k=1)
        self.assertEqual(len(results), 1)

    def test_embedding_failure_propagates(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=RuntimeError('401')):
            with self.assertRaises(RuntimeError):
                vector_store.search_kb(query='router', customer_id=None)


@skipUnless(connection.vendor == 'postgresql', 'pgvector raw-SQL path requires PostgreSQL')
class SearchPostgresTests(_KbDataMixin, TestCase):
    def test_pg_identical_text_scores_highest(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='router power adapter reset', customer_id=None, top_k=5)
        self.assertEqual(results[0]['snippet'], 'router power adapter reset')
        self.assertAlmostEqual(results[0]['score'], 1.0, places=4)

    def test_pg_customer_scoping(self):
        with mock.patch.object(vector_store, 'embed_text', side_effect=_fake_embed_text):
            results = vector_store.search_kb(query='printer paper jam', customer_id=self.customer_a.id, top_k=10)
        sources = {r['source'] for r in results}
        self.assertEqual(sources, {'global.pdf', 'acme.pdf'})


class IngestTests(TestCase):
    def setUp(self):
        self.kb_root = tempfile.mkdtemp(prefix='hongwen-ingest-kb-')
        self.private_root = tempfile.mkdtemp(prefix='hongwen-ingest-private-')
        ov = override_settings(HONGWEN_KB_SOURCES_DIR=self.kb_root, HONGWEN_PRIVATE_MEDIA_ROOT=self.private_root)
        ov.enable()
        self.addCleanup(ov.disable)
        self.customer = Customer.objects.create(name='Acme')

    def _write_source(self, name, text):
        path = os.path.join(self.kb_root, name)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path

    def test_ingest_one_stores_relative_path_and_chunks(self):
        self._write_source('manual.txt', '路由器亮红灯先检查电源适配器。重启设备。')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'manual.txt'), customer_id=self.customer.id)
        self.assertEqual(getattr(doc, 'path'), 'manual.txt')
        self.assertEqual(doc.source_type, 'txt')
        self.assertEqual(doc.customer_id, self.customer.id)
        chunks = KBChunk.objects.filter(document=doc)
        self.assertGreater(chunks.count(), 0)
        self.assertEqual(len(chunks.first().embedding), 1536)
        self.assertEqual(chunks.first().source_path, 'manual.txt')

    def test_reingest_same_file_does_not_duplicate(self):
        self._write_source('manual.txt', '内容一。内容二。')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc1 = ingest.ingest_one(os.path.join(self.kb_root, 'manual.txt'))
            doc2 = ingest.ingest_one(os.path.join(self.kb_root, 'manual.txt'))
        self.assertEqual(doc1.id, doc2.id)
        self.assertEqual(KBDocument.objects.count(), 1)

    def test_qa_pairs_become_qa_chunks(self):
        self._write_source('faq.txt', 'Q: 无法上网怎么办？\nA: 重启路由器。\nQ: 红灯亮？\nA: 检查电源。')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'faq.txt'))
        qa_chunks = KBChunk.objects.filter(document=doc, chunk_type='qa_pair')
        self.assertEqual(qa_chunks.count(), 2)

    def test_replace_document_moves_path_and_removes_old_file(self):
        self._write_source('old.txt', '旧内容')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'old.txt'))
            self._write_source('new.txt', '新内容完全不同')
            doc = ingest.replace_document(document=doc, path=os.path.join(self.kb_root, 'new.txt'), customer_id=None)
        self.assertEqual(getattr(doc, 'path'), 'new.txt')
        self.assertFalse(os.path.exists(os.path.join(self.kb_root, 'old.txt')))
        self.assertTrue(os.path.exists(os.path.join(self.kb_root, 'new.txt')))

    def test_delete_document_removes_file_and_rows(self):
        self._write_source('gone.txt', '将被删除')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'gone.txt'))
        ingest.delete_document(document=doc)
        self.assertFalse(os.path.exists(os.path.join(self.kb_root, 'gone.txt')))
        self.assertEqual(KBDocument.objects.count(), 0)
        self.assertEqual(KBChunk.objects.count(), 0)

    def test_delete_survives_missing_file(self):
        self._write_source('gone.txt', '内容')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'gone.txt'))
        os.remove(os.path.join(self.kb_root, 'gone.txt'))
        ingest.delete_document(document=doc)  # must not raise
        self.assertEqual(KBDocument.objects.count(), 0)

    def test_list_and_preview_and_display_name(self):
        prefixed = '0' * 32 + '_manual.txt'
        self._write_source(prefixed, '第一段内容。第二段内容。')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, prefixed), customer_id=self.customer.id)
        listed = ingest.list_customer_documents(customer_id=self.customer.id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['display_name'], 'manual.txt')
        self.assertGreater(listed[0]['chunk_count'], 0)

        found = ingest.find_customer_document_by_display_name(customer_id=self.customer.id, display_name='manual.txt')
        self.assertEqual(found.id, doc.id)

        preview = ingest.get_document_preview(customer_id=self.customer.id, document_id=doc.id)
        self.assertEqual(preview['display_name'], 'manual.txt')
        self.assertTrue(preview['chunks'])

    def test_customer_boundary_on_get_document(self):
        self._write_source('acme.txt', '内容')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            doc = ingest.ingest_one(os.path.join(self.kb_root, 'acme.txt'), customer_id=self.customer.id)
        other = Customer.objects.create(name='Globex')
        self.assertIsNone(ingest.get_customer_document(customer_id=other.id, document_id=doc.id))
        self.assertIsNotNone(ingest.get_customer_document(customer_id=self.customer.id, document_id=doc.id))

    def test_ingest_directory_skips_underscore_files(self):
        self._write_source('_scratch.txt', '不应入库')
        self._write_source('real.txt', '应入库')
        with mock.patch.object(ingest, 'embed_query', side_effect=stub_vector):
            count = ingest.ingest_directory(self.kb_root)
        self.assertEqual(count, 1)
        self.assertEqual(KBDocument.objects.count(), 1)