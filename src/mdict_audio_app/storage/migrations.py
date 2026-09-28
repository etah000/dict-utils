"""Immutable SQLite schema migrations for the dictionary catalog."""

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            "CREATE TABLE schema_version (version INTEGER NOT NULL)",
            """
            CREATE TABLE dictionary (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, priority INTEGER NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
          source_mdx_name TEXT NOT NULL, source_mdd_name TEXT,
          mdx_sha256 BLOB NOT NULL UNIQUE, mdd_sha256 BLOB,
          metadata_json TEXT NOT NULL, adapter_name TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('IMPORTING','READY','FAILED')),
          imported_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE entry (
          id INTEGER PRIMARY KEY, dictionary_id INTEGER NOT NULL REFERENCES dictionary(id) ON DELETE CASCADE,
          headword TEXT NOT NULL, normalized_headword TEXT NOT NULL,
          definition_html TEXT NOT NULL, definition_text TEXT NOT NULL, source_order INTEGER NOT NULL
            )
            """,
            "CREATE INDEX entry_lookup_idx ON entry(normalized_headword, dictionary_id, source_order)",
            """
            CREATE TABLE example (
          id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
          text TEXT NOT NULL, source_order INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE audio_shard (
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE, byte_size INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL CHECK(status IN ('WRITABLE','SEALED'))
            )
            """,
            """
            CREATE TABLE audio_resource (
          id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, media_type TEXT NOT NULL,
          duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0), byte_size INTEGER NOT NULL,
          shard_id INTEGER NOT NULL REFERENCES audio_shard(id), blob_id INTEGER NOT NULL,
          UNIQUE(shard_id, blob_id)
            )
            """,
            """
            CREATE TABLE audio_link (
          id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
          example_id INTEGER REFERENCES example(id) ON DELETE CASCADE,
          resource_id INTEGER REFERENCES audio_resource(id), kind TEXT NOT NULL,
          resolution_status TEXT NOT NULL, source_ref TEXT NOT NULL, source_order INTEGER NOT NULL
            )
            """,
        ),
    ),
    (
        2,
        (
            "CREATE INDEX example_entry_idx ON example(entry_id, source_order, id)",
            "CREATE INDEX audio_link_entry_idx ON audio_link(entry_id, source_order, id)",
            "CREATE INDEX audio_link_example_idx ON audio_link(example_id)",
            "CREATE INDEX audio_link_resource_idx ON audio_link(resource_id)",
        ),
    ),
)
