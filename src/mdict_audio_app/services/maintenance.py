"""User-triggered integrity, cleanup, recovery, and backup operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Iterable

from mdict_audio_app.storage.audio_store import AudioBlobStore
from mdict_audio_app.storage.catalog import Catalog


@dataclass(frozen=True)
class IntegrityReport:
    """Results of checking the catalog and all registered audio shards."""

    integrity_ok: bool
    catalog_ok: bool
    shard_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackupReport:
    """Description of an atomically published backup."""

    destination: Path
    integrity_ok: bool
    shard_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class MaintenanceService:
    """Keep maintenance work explicit and off the normal import path."""

    def __init__(
        self,
        catalog: Catalog,
        audio_store: AudioBlobStore,
        work_dir: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.audio_store = audio_store
        self.work_dir = Path(work_dir) if work_dir is not None else audio_store.root.parent

    def integrity_check(self) -> IntegrityReport:
        """Check SQLite integrity and that every registered shard is readable."""
        errors: list[str] = []
        catalog_ok = self._check_connection(self.catalog.connection, "dictionary.db", errors)
        shard_files: list[str] = []
        for row in self.catalog.connection.execute("SELECT filename FROM audio_shard ORDER BY id"):
            filename = str(row[0])
            shard_files.append(filename)
            path = self.audio_store.root / filename
            if not path.is_file():
                errors.append(f"missing audio shard: {filename}")
                continue
            try:
                with sqlite3.connect(path) as connection:
                    if not self._check_connection(connection, filename, errors):
                        continue
            except (OSError, sqlite3.Error) as error:
                errors.append(f"{filename}: {error}")
        return IntegrityReport(not errors, catalog_ok, tuple(shard_files), tuple(errors))

    def delete_dictionary(self, dictionary_id: int) -> int:
        """Delete one dictionary and immediately collect unreferenced audio."""
        self.catalog.delete_dictionary(int(dictionary_id))
        return self.audio_store.delete_unreferenced()

    def recover_interrupted_imports(self) -> int:
        """Recover abandoned imports during startup or explicit self-check only.

        Callers must not run this operation concurrently with an active import
        worker; it intentionally changes every ``IMPORTING`` row to ``FAILED``.
        """
        with self.catalog.transaction(immediate=True):
            cursor = self.catalog.connection.execute(
                "UPDATE dictionary SET status = 'FAILED' WHERE status = 'IMPORTING'"
            )
            changed = int(cursor.rowcount)
        for path in self._temporary_work_dirs():
            try:
                shutil.rmtree(path)
            except OSError:
                # Recovery remains useful even when a stale folder is locked.
                continue
        return changed

    def backup(self, destination: Path) -> BackupReport:
        """Publish a consistent catalog/shard snapshot using SQLite backup API."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"backup destination already exists: {destination}")
        temp_path = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
        errors: list[str] = []
        shard_files: tuple[str, ...] = ()
        try:
            # Hold the same write lock used by imports while taking every SQLite
            # snapshot.  This prevents a catalog snapshot from observing half of
            # an import while a shard snapshot observes the other half.
            with self.catalog.transaction(immediate=True):
                shard_files = tuple(str(row[0]) for row in self.catalog.connection.execute(
                    "SELECT filename FROM audio_shard ORDER BY id"
                ))
                # Keep the write-lock connection only for mutual exclusion.  A
                # separate read connection performs the SQLite backup to avoid
                # self-lock/BUSY behavior on some Windows SQLite builds.
                self._backup_file(self._catalog_path(), temp_path / "dictionary.db")
                for filename in shard_files:
                    source = self.audio_store.root / filename
                    if not source.is_file():
                        raise FileNotFoundError(source)
                    self._backup_file(source, temp_path / filename)
                report = self._check_backup(temp_path, shard_files, errors)
                (temp_path / "integrity.json").write_text(
                    json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
                if not report.integrity_ok:
                    raise RuntimeError("backup integrity check failed: " + "; ".join(report.errors))
            os.replace(temp_path, destination)
            return BackupReport(destination, True, shard_files, tuple(errors))
        except BaseException:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise

    @staticmethod
    def _backup_connection(source: sqlite3.Connection, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            source.backup(target)

    def _backup_file(self, source: Path, destination: Path) -> None:
        with sqlite3.connect(source) as connection:
            self._backup_connection(connection, destination)

    def _catalog_path(self) -> Path:
        row = self.catalog.connection.execute("PRAGMA database_list").fetchone()
        if row is None or not row[2] or str(row[2]) == ":memory:":
            raise ValueError("catalog backup requires a file-backed SQLite database")
        return Path(str(row[2]))

    @staticmethod
    def _check_connection(connection: sqlite3.Connection, name: str, errors: list[str]) -> bool:
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            ok = result is not None and str(result[0]).lower() == "ok"
            if not ok:
                errors.append(f"{name}: {result[0] if result else 'no result'}")
            return ok
        except sqlite3.Error as error:
            errors.append(f"{name}: {error}")
            return False

    def _check_backup(
        self, root: Path, shard_files: Iterable[str], errors: list[str]
    ) -> IntegrityReport:
        with sqlite3.connect(root / "dictionary.db") as connection:
            catalog_ok = self._check_connection(connection, "dictionary.db", errors)
        for filename in shard_files:
            path = root / filename
            if not path.is_file():
                errors.append(f"missing backup shard: {filename}")
                continue
            with sqlite3.connect(path) as connection:
                self._check_connection(connection, filename, errors)
        return IntegrityReport(not errors, catalog_ok, tuple(shard_files), tuple(errors))

    def _temporary_work_dirs(self) -> tuple[Path, ...]:
        if not self.work_dir.exists():
            return ()
        return tuple(
            path for path in self.work_dir.iterdir()
            if path.is_dir() and (
                path.name.startswith(".mdict-import-")
                or path.name.startswith("mdict-import-")
                or path.name.startswith(".import-")
            )
        )
