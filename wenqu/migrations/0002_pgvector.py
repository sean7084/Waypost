"""Enable the pgvector extension on PostgreSQL (no-op on other backends).

Wenqu stores embeddings as JSONField (jsonb) lists, not native ``vector``
columns, so this migration ONLY creates the extension - retrieval casts at query
time (``embedding::text::vector <=> CAST(:q AS vector)``) and needs the ``vector``
type plus its operators to exist. An ivfflat index requires a real ``vector``
column and cannot be built on jsonb, so it is deliberately deferred as
performance debt (the KB is small enough for a sequential cosine scan).

The vendor guard is implemented with RunPython rather than a bare RunSQL because
RunSQL has no conditional form: on SQLite (local dev / the default test backend)
``CREATE EXTENSION`` is invalid syntax and must be skipped entirely. The reverse
is a no-op - dropping a shared extension could break other databases/objects,
and leaving it installed is harmless.
"""
from django.db import migrations


def create_vector_extension(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('CREATE EXTENSION IF NOT EXISTS vector')


class Migration(migrations.Migration):

    dependencies = [
        ('wenqu', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_vector_extension, reverse_code=migrations.RunPython.noop),
    ]