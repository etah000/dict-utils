"""Transactional access to the local dictionary catalog."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Sequence

from mdict_audio_app.domain import AudioKind, ResolutionStatus, normalize_headword

from .migrations import MIGRATIONS


class Catalog:
    """A SQLite catalog whose schema is migrated when opened."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @classmethod
    def open(cls, path: Path) -> Catalog:
        """Open *path*, configure SQLite, and apply outstanding migrations."""
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        catalog = cls(connection)
        catalog._apply_migrations()
        return catalog

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.connection.close()

    def _apply_migrations(self) -> None:
        schema_exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        applied_versions: set[int] = set()
        if schema_exists:
            applied_versions = {
                row[0] for row in self.connection.execute("SELECT version FROM schema_version")
            }

        for version, statements in MIGRATIONS:
            if version not in applied_versions:
                with self.transaction():
                    for statement in statements:
                        self.connection.execute(statement)
                    self.connection.execute(
                        "INSERT INTO schema_version (version) VALUES (?)", (version,)
                    )

    def table_names(self) -> set[str]:
        """Return the set of user-visible table names in the current database."""
        return {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    def schema_version(self) -> int:
        """Return the most recently applied schema migration version."""
        row = self.connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row[0] is not None else 0

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Catalog]:
        """Group catalog writes into a transaction that rolls back on errors.

        ``immediate=True`` acquires SQLite's reserved write lock before reads that
        must be serialized with their following writes.
        """
        self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield self
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def count(self, table_name: str) -> int:
        """Count rows in a catalog table, rejecting unknown table names."""
        if table_name not in self.table_names():
            raise ValueError(f"unknown catalog table: {table_name}")
        return int(self.connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])

    def create_dictionary(
        self,
        *,
        name: str,
        mdx_sha256: bytes,
        source_mdx_name: str,
        source_mdd_name: str | None = None,
        mdd_sha256: bytes | None = None,
        metadata: dict[str, object] | None = None,
        adapter_name: str = "default",
        status: str = "IMPORTING",
        enabled: bool = True,
    ) -> int:
        """Add a dictionary with the next available priority."""
        priority = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(priority), -1) + 1 FROM dictionary"
            ).fetchone()[0]
        )
        cursor = self.connection.execute(
            """
            INSERT INTO dictionary (
                name, priority, enabled, source_mdx_name, source_mdd_name,
                mdx_sha256, mdd_sha256, metadata_json, adapter_name, status, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                priority,
                int(enabled),
                source_mdx_name,
                source_mdd_name,
                mdx_sha256,
                mdd_sha256,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                adapter_name,
                status,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return int(cursor.lastrowid)

    def insert_entry(
        self,
        *,
        dictionary_id: int,
        headword: str,
        definition_html: str,
        definition_text: str,
        source_order: int,
    ) -> int:
        """Store a dictionary entry and its lookup-normalized headword."""
        cursor = self.connection.execute(
            """
            INSERT INTO entry (
                dictionary_id, headword, normalized_headword,
                definition_html, definition_text, source_order
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dictionary_id,
                headword,
                normalize_headword(headword),
                definition_html,
                definition_text,
                source_order,
            ),
        )
        return int(cursor.lastrowid)

    def create_audio_shard(
        self, *, filename: str, byte_size: int = 0, status: str = "WRITABLE"
    ) -> int:
        """Register an audio shard that can contain resource blobs."""
        cursor = self.connection.execute(
            "INSERT INTO audio_shard (filename, byte_size, status) VALUES (?, ?, ?)",
            (filename, byte_size, status),
        )
        return int(cursor.lastrowid)

    def register_audio(
        self,
        *,
        sha256: bytes,
        media_type: str,
        duration_ms: int,
        byte_size: int,
        shard_id: int,
        blob_id: int,
    ) -> int:
        """Register an addressable audio resource in an existing shard."""
        cursor = self.connection.execute(
            """
            INSERT INTO audio_resource (
                sha256, media_type, duration_ms, byte_size, shard_id, blob_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sha256, media_type, duration_ms, byte_size, shard_id, blob_id),
        )
        return int(cursor.lastrowid)

    def resolve_audio_link(
        self,
        *,
        entry_id: int,
        resource_id: int | None,
        kind: AudioKind,
        resolution_status: ResolutionStatus,
        source_ref: str,
        source_order: int,
        example_id: int | None = None,
    ) -> int:
        """Persist an audio resolution result for an entry or one of its examples."""
        cursor = self.connection.execute(
            """
            INSERT INTO audio_link (
                entry_id, example_id, resource_id, kind, resolution_status, source_ref, source_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                example_id,
                resource_id,
                str(kind),
                str(resolution_status),
                source_ref,
                source_order,
            ),
        )
        return int(cursor.lastrowid)

    def list_dictionaries(self) -> list[sqlite3.Row]:
        """List dictionaries in their current selection order."""
        return list(
            self.connection.execute("SELECT * FROM dictionary ORDER BY priority, id")
        )

    def dictionary_summaries(self) -> list[sqlite3.Row]:
        """Return management rows with entry and resolved-audio counts."""
        return list(self.connection.execute("""
            SELECT d.*, COUNT(DISTINCT e.id) AS entry_count,
                   COUNT(DISTINCT CASE WHEN l.resource_id IS NOT NULL THEN l.id END) AS audio_count
            FROM dictionary AS d
            LEFT JOIN entry AS e ON e.dictionary_id = d.id
            LEFT JOIN audio_link AS l ON l.entry_id = e.id
            GROUP BY d.id
            ORDER BY d.priority, d.id
        """))

    def dictionary_priorities(self) -> list[int]:
        """Return dictionary IDs in selection order."""
        return [int(row["id"]) for row in self.list_dictionaries()]

    def dictionary_delete_summary(self, ids: Sequence[int]) -> list[sqlite3.Row]:
        """Return entry and linked-audio counts for a deletion confirmation."""
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return list(self.connection.execute(f"""
            SELECT d.id, d.name, COUNT(DISTINCT e.id) AS entry_count,
                   COUNT(DISTINCT CASE WHEN l.resource_id IS NOT NULL THEN l.id END) AS audio_count
            FROM dictionary d LEFT JOIN entry e ON e.dictionary_id = d.id
            LEFT JOIN audio_link l ON l.entry_id = e.id
            WHERE d.id IN ({marks}) GROUP BY d.id ORDER BY d.priority, d.id
        """, tuple(ids)))

    def set_dictionary_priorities(self, ids: Sequence[int]) -> None:
        """Replace priority ordering with the supplied complete dictionary id sequence."""
        with self.transaction(immediate=True):
            existing_ids = [row[0] for row in self.connection.execute("SELECT id FROM dictionary")]
            if len(ids) != len(existing_ids) or set(ids) != set(existing_ids):
                raise ValueError("ids must contain every dictionary exactly once")
            for priority, dictionary_id in enumerate(ids):
                self.connection.execute(
                    "UPDATE dictionary SET priority = ? WHERE id = ?",
                    (priority, dictionary_id),
                )

    def delete_dictionary(self, dictionary_id: int) -> None:
        """Delete a dictionary and compact the remaining priorities."""
        with self.transaction():
            self.connection.execute("DELETE FROM dictionary WHERE id = ?", (dictionary_id,))
            rows = self.connection.execute("SELECT id FROM dictionary ORDER BY priority, id").fetchall()
            for priority, row in enumerate(rows):
                self.connection.execute("UPDATE dictionary SET priority = ? WHERE id = ?", (priority, row[0]))


@dataclass(frozen=True)
class CatalogFactory:
    """Open catalogs at a configured filesystem path."""

    path: Path

    def open(self) -> Catalog:
        """Open the configured catalog path."""
        return Catalog.open(self.path)
