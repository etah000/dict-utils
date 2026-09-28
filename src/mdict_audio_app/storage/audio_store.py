"""Content-addressed audio blobs stored in bounded SQLite shard files."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterator

from .catalog import Catalog


@dataclass(frozen=True)
class CleanupFailure:
    """A physical shard cleanup error retained for maintenance reporting."""

    resource_id: int | None
    path: Path
    error: str


@dataclass(frozen=True)
class BlobSnapshot:
    """A physical orphan candidate captured while the catalog write lock is held."""

    resource_id: int | None
    shard_id: int
    blob_id: int
    byte_size: int
    path: Path


class AudioBlobStore:
    """Store audio bytes separately from the catalog while retaining stable resource ids."""

    def __init__(self, root: Path, catalog: Catalog, *, max_shard_bytes: int) -> None:
        if max_shard_bytes <= 0:
            raise ValueError("max_shard_bytes must be positive")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.catalog = catalog
        self.max_shard_bytes = max_shard_bytes
        self.last_cleanup_failures: tuple[CleanupFailure, ...] = ()

    def put(self, data: bytes, media_type: str, duration_ms: int) -> int:
        """Persist *data* once and return its catalog resource id."""
        if len(data) > self.max_shard_bytes:
            raise ValueError("audio data exceeds max_shard_bytes")
        digest = sha256(data).digest()
        existing = self._find_resource_by_sha256(digest)
        if existing is not None:
            return existing

        registration_error: BaseException | None = None
        resource_id: int | None = None
        with self.catalog.transaction(immediate=True):
            existing = self._find_resource_by_sha256(digest)
            if existing is not None:
                return existing
            shard_id, filename = self._select_or_create_shard(
                len(data), in_transaction=True
            )
            blob_id, inserted = self._write_blob(self._shard_path(filename), digest, data)
            if not inserted:
                self._release_shard_bytes(
                    shard_id, len(data), in_transaction=True
                )
            try:
                resource_id = self.catalog.register_audio(
                    sha256=digest,
                    media_type=media_type,
                    duration_ms=duration_ms,
                    byte_size=len(data),
                    shard_id=shard_id,
                    blob_id=blob_id,
                )
            except BaseException as error:
                # Commit the shard reservation so the now-physical orphan remains
                # discoverable by a later cleanup scan, then report the real error.
                registration_error = error
        if registration_error is not None:
            raise registration_error
        assert resource_id is not None
        return resource_id

    def read(self, resource_id: int) -> bytes:
        """Read a cataloged resource, raising ``KeyError`` if it is absent."""
        row = self.catalog.connection.execute(
            """
            SELECT audio_shard.filename, audio_resource.blob_id
            FROM audio_resource
            JOIN audio_shard ON audio_shard.id = audio_resource.shard_id
            WHERE audio_resource.id = ?
            """,
            (resource_id,),
        ).fetchone()
        if row is None:
            raise KeyError(resource_id)

        shard_path = self._shard_path(row["filename"])
        if not shard_path.is_file():
            raise KeyError(resource_id)
        with self._shard_connection(shard_path) as connection:
            blob = connection.execute(
                "SELECT data FROM audio_blob WHERE id = ?", (row["blob_id"],)
            ).fetchone()
        if blob is None:
            raise KeyError(resource_id)
        return bytes(blob[0])

    def delete_unreferenced(self) -> int:
        """Remove resources without links and their blobs, returning catalog deletions.

        Catalog resource deletion commits before physical deletion.  Candidates are
        captured while holding the catalog write lock, then rechecked under that lock
        immediately before their physical rows are removed.
        """
        failures: list[CleanupFailure] = []
        with self.catalog.transaction(immediate=True):
            rows = list(
                self.catalog.connection.execute(
                    """
                    SELECT audio_resource.id, audio_resource.blob_id,
                           audio_shard.id AS shard_id
                    FROM audio_resource
                    JOIN audio_shard ON audio_shard.id = audio_resource.shard_id
                    LEFT JOIN audio_link ON audio_link.resource_id = audio_resource.id
                    WHERE audio_link.id IS NULL
                    """
                )
            )
            removed_ids = {
                (int(row["shard_id"]), int(row["blob_id"])): int(row["id"])
                for row in rows
            }
            registered = {
                (int(row["shard_id"]), int(row["blob_id"]))
                for row in self.catalog.connection.execute(
                    "SELECT shard_id, blob_id FROM audio_resource"
                )
            }
            snapshots = self._snapshot_unregistered_blobs(
                registered - set(removed_ids), removed_ids, failures
            )
            for row in rows:
                self.catalog.connection.execute(
                    "DELETE FROM audio_resource WHERE id = ?", (int(row["id"]),)
                )
        self._delete_unregistered_blobs(snapshots, failures)
        self.last_cleanup_failures = tuple(failures)
        return len(rows)

    def _find_resource_by_sha256(self, digest: bytes) -> int | None:
        row = self.catalog.connection.execute(
            "SELECT id FROM audio_resource WHERE sha256 = ?", (digest,)
        ).fetchone()
        return None if row is None else int(row["id"])

    def _select_or_create_shard(
        self, byte_size: int, *, in_transaction: bool = False
    ) -> tuple[int, str]:
        if in_transaction:
            return self._select_or_create_shard_in_transaction(byte_size)
        with self.catalog.transaction(immediate=True):
            return self._select_or_create_shard_in_transaction(byte_size)

    def _select_or_create_shard_in_transaction(self, byte_size: int) -> tuple[int, str]:
        row = self.catalog.connection.execute(
            """
            SELECT id, filename FROM audio_shard
            WHERE status = 'WRITABLE' AND byte_size + ? <= ?
            ORDER BY id DESC LIMIT 1
            """,
            (byte_size, self.max_shard_bytes),
        ).fetchone()
        if row is not None:
            reserved = self.catalog.connection.execute(
                """
                UPDATE audio_shard
                SET byte_size = byte_size + ?
                WHERE id = ? AND status = 'WRITABLE'
                  AND byte_size + ? <= ?
                """,
                (byte_size, row["id"], byte_size, self.max_shard_bytes),
            )
            if reserved.rowcount == 1:
                return int(row["id"]), str(row["filename"])
        self.catalog.connection.execute(
            "UPDATE audio_shard SET status = 'SEALED' WHERE status = 'WRITABLE'"
        )
        filename = self._next_shard_filename()
        shard_id = self.catalog.create_audio_shard(
            filename=filename, byte_size=byte_size
        )
        return shard_id, filename

    def _next_shard_filename(self) -> str:
        index = 1
        while True:
            filename = f"audio-{index:03d}.db"
            exists = self.catalog.connection.execute(
                "SELECT 1 FROM audio_shard WHERE filename = ?", (filename,)
            ).fetchone()
            if exists is None and not self._shard_path(filename).exists():
                return filename
            index += 1

    def _write_blob(self, shard_path: Path, digest: bytes, data: bytes) -> tuple[int, bool]:
        with self._shard_connection(shard_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audio_blob (
                    id INTEGER PRIMARY KEY,
                    sha256 BLOB NOT NULL UNIQUE,
                    data BLOB NOT NULL
                )
                """
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO audio_blob (sha256, data) VALUES (?, ?)",
                (digest, data),
            )
            row = connection.execute(
                "SELECT id FROM audio_blob WHERE sha256 = ?", (digest,)
            ).fetchone()
        assert row is not None
        return int(row[0]), cursor.rowcount == 1

    @staticmethod
    def _delete_blob(connection: sqlite3.Connection, blob_id: int) -> None:
        connection.execute("DELETE FROM audio_blob WHERE id = ?", (blob_id,))

    def _snapshot_unregistered_blobs(
        self,
        registered: set[tuple[int, int]],
        removed_ids: dict[tuple[int, int], int],
        failures: list[CleanupFailure],
    ) -> list[BlobSnapshot]:
        snapshots: list[BlobSnapshot] = []
        shards = list(self.catalog.connection.execute("SELECT id, filename FROM audio_shard"))
        for shard in shards:
            shard_id = int(shard["id"])
            try:
                shard_path = self._shard_path(shard["filename"])
                if not shard_path.is_file():
                    raise FileNotFoundError(shard_path)
                with self._shard_connection(shard_path) as connection:
                    blobs = list(connection.execute("SELECT id, length(data) FROM audio_blob"))
                    for blob in blobs:
                        blob_id = int(blob[0])
                        if (shard_id, blob_id) in registered:
                            continue
                        snapshots.append(
                            BlobSnapshot(
                                removed_ids.get((shard_id, blob_id)),
                                shard_id,
                                blob_id,
                                int(blob[1]),
                                shard_path,
                            )
                        )
            except (OSError, sqlite3.Error, ValueError) as error:
                failures.append(CleanupFailure(None, self.root / str(shard["filename"]), str(error)))
        return snapshots

    def _delete_unregistered_blobs(
        self, snapshots: list[BlobSnapshot], failures: list[CleanupFailure]
    ) -> None:
        for snapshot in snapshots:
            with self.catalog.transaction(immediate=True):
                still_registered = self.catalog.connection.execute(
                    "SELECT 1 FROM audio_resource WHERE shard_id = ? AND blob_id = ?",
                    (snapshot.shard_id, snapshot.blob_id),
                ).fetchone()
                if still_registered is not None:
                    continue
                try:
                    if not snapshot.path.is_file():
                        raise FileNotFoundError(snapshot.path)
                    with self._shard_connection(snapshot.path) as connection:
                        self._delete_blob(connection, snapshot.blob_id)
                except (OSError, sqlite3.Error, ValueError) as error:
                    failures.append(
                        CleanupFailure(snapshot.resource_id, snapshot.path, str(error))
                    )
                    continue
                self._release_shard_bytes(
                    snapshot.shard_id, snapshot.byte_size, in_transaction=True
                )

    def _release_shard_bytes(
        self, shard_id: int, byte_size: int, *, in_transaction: bool = False
    ) -> None:
        if in_transaction:
            self._release_shard_bytes_in_transaction(shard_id, byte_size)
            return
        with self.catalog.transaction(immediate=True):
            self._release_shard_bytes_in_transaction(shard_id, byte_size)

    def _release_shard_bytes_in_transaction(self, shard_id: int, byte_size: int) -> None:
        self.catalog.connection.execute(
            """
            UPDATE audio_shard
            SET byte_size = CASE
                WHEN byte_size >= ? THEN byte_size - ?
                ELSE 0
            END
            WHERE id = ?
            """,
            (byte_size, byte_size, shard_id),
        )

    @contextmanager
    def _shard_connection(self, shard_path: Path) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(shard_path)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _shard_path(self, filename: str) -> Path:
        candidate = (self.root / filename).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError("audio shard path escapes storage root") from error
        return candidate
