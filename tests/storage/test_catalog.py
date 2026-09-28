"""Integration tests for the SQLite-backed dictionary catalog."""

import sqlite3

import pytest

from mdict_audio_app.domain import AudioKind, ResolutionStatus
import mdict_audio_app.storage.catalog as catalog_module
from mdict_audio_app.storage.catalog import Catalog, CatalogFactory


def test_open_creates_versioned_schema(tmp_path):
    """Removing a required migration table must make this schema contract fail."""
    catalog = Catalog.open(tmp_path / "dictionary.db")
    try:
        tables = catalog.table_names()
        assert {
            "dictionary",
            "entry",
            "example",
            "audio_resource",
            "audio_link",
            "audio_shard",
        } <= tables
        assert catalog.schema_version() == 2
    finally:
        catalog.close()


def test_failed_migration_leaves_no_partial_schema_and_can_retry(tmp_path, monkeypatch):
    """A migration failure must roll back its DDL and allow the original migration to retry."""
    database_path = tmp_path / "retry.db"
    original_migrations = catalog_module.MIGRATIONS
    failing_migrations = (
        (
            1,
            (
                "CREATE TABLE schema_version (version INTEGER NOT NULL)",
                "CREATE TABLE transient_table (id INTEGER PRIMARY KEY)",
                "CREATE TABL deliberately_invalid (id INTEGER PRIMARY KEY)",
            ),
        ),
    )
    monkeypatch.setattr(catalog_module, "MIGRATIONS", failing_migrations)

    with pytest.raises(sqlite3.OperationalError):
        Catalog.open(database_path)

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == []
    finally:
        connection.close()

    monkeypatch.setattr(catalog_module, "MIGRATIONS", original_migrations)
    catalog = Catalog.open(database_path)
    try:
        assert catalog.schema_version() == 2
        assert "transient_table" not in catalog.table_names()
    finally:
        catalog.close()


def test_factory_opens_catalog_at_requested_path(tmp_path):
    """Ignoring the factory path must make the created database unavailable there."""
    database_path = tmp_path / "factory.db"
    catalog = CatalogFactory(database_path).open()
    try:
        assert database_path.exists()
        assert catalog.schema_version() == 2
    finally:
        catalog.close()


def test_transaction_rolls_back_all_rows(catalog):
    """Committing a failed transaction would leave the inserted dictionary behind."""
    try:
        with catalog.transaction():
            catalog.create_dictionary(
                name="A", mdx_sha256=b"a" * 32, source_mdx_name="a.mdx"
            )
            raise RuntimeError("stop")
    except RuntimeError:
        pass

    assert catalog.count("dictionary") == 0


def test_create_dictionary_assigns_priority_and_utc_timestamp(catalog):
    """Dropping priority assignment or import time persistence breaks import ordering."""
    first_id = catalog.create_dictionary(
        name="First", mdx_sha256=b"a" * 32, source_mdx_name="first.mdx"
    )
    second_id = catalog.create_dictionary(
        name="Second", mdx_sha256=b"b" * 32, source_mdx_name="second.mdx"
    )

    dictionaries = catalog.list_dictionaries()
    assert [(row["id"], row["priority"]) for row in dictionaries] == [
        (first_id, 0),
        (second_id, 1),
    ]
    assert dictionaries[0]["imported_at"].endswith("+00:00")


def test_insert_entry_normalizes_headword(catalog):
    """Skipping normalization would prevent equivalent lookup spellings from matching."""
    dictionary_id = catalog.create_dictionary(
        name="A", mdx_sha256=b"a" * 32, source_mdx_name="a.mdx"
    )

    entry_id = catalog.insert_entry(
        dictionary_id=dictionary_id,
        headword="  CAF\u00c9\u3000WORD  ",
        definition_html="<p>meaning</p>",
        definition_text="meaning",
        source_order=4,
    )

    row = catalog.connection.execute(
        "SELECT id, normalized_headword FROM entry WHERE id = ?", (entry_id,)
    ).fetchone()
    assert dict(row) == {"id": entry_id, "normalized_headword": "caf\u00e9 word"}


def test_register_and_resolve_audio_link(catalog):
    """Failing to persist audio linkage would lose a resolved pronunciation."""
    dictionary_id = catalog.create_dictionary(
        name="A", mdx_sha256=b"a" * 32, source_mdx_name="a.mdx"
    )
    entry_id = catalog.insert_entry(
        dictionary_id=dictionary_id,
        headword="word",
        definition_html="<p>meaning</p>",
        definition_text="meaning",
        source_order=0,
    )
    shard_id = catalog.create_audio_shard(filename="audio-001.bin")
    resource_id = catalog.register_audio(
        sha256=b"r" * 32,
        media_type="audio/mpeg",
        duration_ms=1250,
        byte_size=8192,
        shard_id=shard_id,
        blob_id=7,
    )

    link_id = catalog.resolve_audio_link(
        entry_id=entry_id,
        resource_id=resource_id,
        kind=AudioKind.HEADWORD,
        resolution_status=ResolutionStatus.MATCHED,
        source_ref="word.mp3",
        source_order=0,
    )

    row = catalog.connection.execute(
        "SELECT id, resource_id, kind, resolution_status FROM audio_link WHERE id = ?",
        (link_id,),
    ).fetchone()
    assert dict(row) == {
        "id": link_id,
        "resource_id": resource_id,
        "kind": "HEADWORD",
        "resolution_status": "MATCHED",
    }


def test_set_dictionary_priorities_reorders_enabled_dictionaries(catalog):
    """Ignoring requested ids would keep selection precedence in the wrong order."""
    first_id = catalog.create_dictionary(
        name="First", mdx_sha256=b"a" * 32, source_mdx_name="first.mdx"
    )
    second_id = catalog.create_dictionary(
        name="Second", mdx_sha256=b"b" * 32, source_mdx_name="second.mdx"
    )

    catalog.set_dictionary_priorities([second_id, first_id])

    assert [row["id"] for row in catalog.list_dictionaries()] == [second_id, first_id]


def test_dictionary_priorities_and_delete_compact_order(catalog):
    first_id = catalog.create_dictionary(name="First", mdx_sha256=b"c" * 32, source_mdx_name="c.mdx")
    second_id = catalog.create_dictionary(name="Second", mdx_sha256=b"d" * 32, source_mdx_name="d.mdx")
    catalog.set_dictionary_priorities([second_id, first_id])
    assert catalog.dictionary_priorities() == [second_id, first_id]
    catalog.delete_dictionary(second_id)
    assert catalog.dictionary_priorities() == [first_id]
