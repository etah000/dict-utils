"""Integration tests for sharded, content-addressed audio storage."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest

from mdict_audio_app.storage.audio_store import AudioBlobStore
from mdict_audio_app.storage.catalog import Catalog


@pytest.fixture
def store(catalog, tmp_path):
    """Provide a real blob store backed by the test catalog."""
    return AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=6)


def test_put_deduplicates_and_rotates(catalog, tmp_path):
    """Skipping hash reuse or capacity rotation puts audio in the wrong shard."""
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=6)

    first = store.put(b"abc", "audio/mpeg", 100)
    duplicate = store.put(b"abc", "audio/mpeg", 100)
    second = store.put(b"defg", "audio/wav", 200)

    first_location = catalog.connection.execute(
        "SELECT shard_id FROM audio_resource WHERE id = ?", (first,)
    ).fetchone()
    second_location = catalog.connection.execute(
        "SELECT shard_id FROM audio_resource WHERE id = ?", (second,)
    ).fetchone()
    assert first == duplicate
    assert store.read(first) == b"abc"
    assert first_location["shard_id"] != second_location["shard_id"]


def test_delete_unreferenced_removes_catalog_and_blob(catalog, store):
    """Leaving an unlinked catalog row or blob makes cleanup incomplete."""
    resource_id = store.put(b"orphan", "audio/mpeg", 50)
    location = catalog.connection.execute(
        """
        SELECT audio_shard.filename, audio_resource.blob_id
        FROM audio_resource
        JOIN audio_shard ON audio_shard.id = audio_resource.shard_id
        WHERE audio_resource.id = ?
        """,
        (resource_id,),
    ).fetchone()

    assert store.delete_unreferenced() == 1
    assert catalog.connection.execute(
        "SELECT 1 FROM audio_resource WHERE id = ?", (resource_id,)
    ).fetchone() is None
    with sqlite3.connect(store.root / location["filename"]) as connection:
        assert connection.execute(
            "SELECT 1 FROM audio_blob WHERE id = ?", (location["blob_id"],)
        ).fetchone() is None
    with pytest.raises(KeyError):
        store.read(resource_id)
    assert catalog.connection.execute(
        "SELECT byte_size FROM audio_shard WHERE filename = ?", (location["filename"],)
    ).fetchone()["byte_size"] == 0


def test_put_skips_preexisting_unregistered_shard_path(catalog, tmp_path):
    """Opening an unregistered shard filename would overwrite user-owned data."""
    root = tmp_path / "audio"
    root.mkdir()
    protected = root / "audio-001.db"
    with sqlite3.connect(protected) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('keep')")

    store = AudioBlobStore(root, catalog, max_shard_bytes=6)
    store.put(b"abc", "audio/mpeg", 100)

    with sqlite3.connect(protected) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == "keep"
    assert catalog.connection.execute("SELECT filename FROM audio_shard").fetchone()[
        "filename"
    ] == "audio-002.db"


def test_put_rejects_data_larger_than_one_shard(catalog, tmp_path):
    """Creating an over-capacity shard defeats the configured capacity bound."""
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=3)

    with pytest.raises(ValueError, match="max_shard_bytes"):
        store.put(b"four", "audio/mpeg", 100)

    assert catalog.count("audio_shard") == 0


def test_new_store_cleans_blob_orphaned_by_catalog_registration_failure(
    catalog, tmp_path, monkeypatch
):
    """A new process must collect blobs left by a rolled-back catalog registration."""
    root = tmp_path / "audio"
    store = AudioBlobStore(root, catalog, max_shard_bytes=6)

    def fail_registration(**_kwargs):
        raise sqlite3.OperationalError("catalog unavailable")

    monkeypatch.setattr(catalog, "register_audio", fail_registration)
    with pytest.raises(sqlite3.OperationalError, match="catalog unavailable"):
        store.put(b"orphan", "audio/mpeg", 50)
    monkeypatch.undo()

    fresh_store = AudioBlobStore(root, catalog, max_shard_bytes=6)
    assert fresh_store.delete_unreferenced() == 0
    with sqlite3.connect(root / "audio-001.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM audio_blob").fetchone()[0] == 0
    assert catalog.connection.execute(
        "SELECT byte_size FROM audio_shard WHERE filename = 'audio-001.db'"
    ).fetchone()["byte_size"] == 0


def test_new_store_retries_physical_orphan_cleanup(catalog, store, monkeypatch):
    """A process restart must not lose the only retry path after blob deletion fails."""
    resource_id = store.put(b"retry", "audio/mpeg", 50)
    location = catalog.connection.execute(
        """
        SELECT audio_shard.filename, audio_resource.blob_id
        FROM audio_resource
        JOIN audio_shard ON audio_shard.id = audio_resource.shard_id
        WHERE audio_resource.id = ?
        """,
        (resource_id,),
    ).fetchone()
    def fail_physical_delete(*_args):
        raise sqlite3.OperationalError("locked shard")

    monkeypatch.setattr(AudioBlobStore, "_delete_blob", staticmethod(fail_physical_delete))
    assert store.delete_unreferenced() == 1
    assert store.last_cleanup_failures[0].resource_id == resource_id
    assert store.last_cleanup_failures[0].error == "locked shard"
    monkeypatch.undo()

    fresh_store = AudioBlobStore(store.root, catalog, max_shard_bytes=6)
    assert fresh_store.delete_unreferenced() == 0
    with sqlite3.connect(store.root / location["filename"]) as connection:
        assert connection.execute(
            "SELECT 1 FROM audio_blob WHERE id = ?", (location["blob_id"],)
        ).fetchone() is None


def test_cleanup_removes_duplicate_hash_blob_without_catalog_resource(catalog, tmp_path):
    """A physical copy in another shard must not survive solely because its hash exists."""
    root = tmp_path / "audio"
    store = AudioBlobStore(root, catalog, max_shard_bytes=6)
    store.put(b"same", "audio/mpeg", 100)
    orphan_shard_id = catalog.create_audio_shard(filename="audio-099.db")
    orphan_path = root / "audio-099.db"
    with sqlite3.connect(orphan_path) as connection:
        connection.execute(
            "CREATE TABLE audio_blob (id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, data BLOB NOT NULL)"
        )
        connection.execute(
            "INSERT INTO audio_blob (sha256, data) VALUES (?, ?)",
            (sha256(b"same").digest(), b"same"),
        )
    catalog.connection.execute(
        "UPDATE audio_shard SET byte_size = 4 WHERE id = ?", (orphan_shard_id,)
    )

    assert AudioBlobStore(root, catalog, max_shard_bytes=6).delete_unreferenced() == 1
    with sqlite3.connect(orphan_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audio_blob").fetchone()[0] == 0


def test_two_catalog_connections_reserve_shard_capacity_atomically(tmp_path, monkeypatch):
    """Splitting selection from reservation lets concurrent writers exceed one shard."""
    catalog_path = tmp_path / "dictionary.db"
    root = tmp_path / "audio"
    bootstrap = Catalog.open(catalog_path)
    try:
        bootstrap.create_audio_shard(filename="audio-001.db")
    finally:
        bootstrap.close()

    first_blob_write = threading.Event()
    allow_first_registration = threading.Event()
    original_write_blob = AudioBlobStore._write_blob

    def delayed_write_blob(self, *args):
        result = original_write_blob(self, *args)
        if not first_blob_write.is_set():
            first_blob_write.set()
            assert allow_first_registration.wait(timeout=5)
        return result

    monkeypatch.setattr(AudioBlobStore, "_write_blob", delayed_write_blob)

    def put_from_separate_connection(data):
        catalog = Catalog.open(catalog_path)
        try:
            return AudioBlobStore(root, catalog, max_shard_bytes=6).put(
                data, "audio/mpeg", 100
            )
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(put_from_separate_connection, b"abcd")
        assert first_blob_write.wait(timeout=5)
        second = executor.submit(put_from_separate_connection, b"efgh")
        allow_first_registration.set()
        resource_ids = [first.result(timeout=5), second.result(timeout=5)]

    catalog = Catalog.open(catalog_path)
    try:
        assert all(
            row["byte_size"] <= 6
            for row in catalog.connection.execute("SELECT byte_size FROM audio_shard")
        )
        store = AudioBlobStore(root, catalog, max_shard_bytes=6)
        assert {store.read(resource_id) for resource_id in resource_ids} == {b"abcd", b"efgh"}
    finally:
        catalog.close()


def test_failed_blob_write_releases_reserved_capacity(catalog, tmp_path, monkeypatch):
    """A failed physical write must not consume capacity that contains no blob."""
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=6)

    def fail_blob_write(*_args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(AudioBlobStore, "_write_blob", fail_blob_write)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        store.put(b"abcd", "audio/mpeg", 100)
    assert catalog.connection.execute(
        "SELECT COALESCE(SUM(byte_size), 0) AS size FROM audio_shard"
    ).fetchone()["size"] == 0
    monkeypatch.undo()

    resource_id = store.put(b"abcd", "audio/mpeg", 100)
    assert store.read(resource_id) == b"abcd"


def test_two_catalog_connections_deduplicate_after_physical_write(tmp_path, monkeypatch):
    """A waiting duplicate writer must observe the winner instead of adding a row."""
    catalog_path = tmp_path / "dictionary.db"
    root = tmp_path / "audio"
    bootstrap = Catalog.open(catalog_path)
    bootstrap.close()

    blob_written = threading.Event()
    allow_registration = threading.Event()
    original_write_blob = AudioBlobStore._write_blob

    def synchronized_write_blob(self, *args):
        result = original_write_blob(self, *args)
        blob_written.set()
        assert allow_registration.wait(timeout=5)
        return result

    monkeypatch.setattr(AudioBlobStore, "_write_blob", synchronized_write_blob)

    def put_from_separate_connection():
        catalog = Catalog.open(catalog_path)
        try:
            return AudioBlobStore(root, catalog, max_shard_bytes=6).put(
                b"same", "audio/mpeg", 100
            )
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(put_from_separate_connection)
        assert blob_written.wait(timeout=5)
        loser = executor.submit(put_from_separate_connection)
        allow_registration.set()
        resource_ids = [winner.result(timeout=5), loser.result(timeout=5)]

    catalog = Catalog.open(catalog_path)
    try:
        assert resource_ids[0] == resource_ids[1]
        assert catalog.count("audio_resource") == 1
        assert AudioBlobStore(root, catalog, max_shard_bytes=6).read(resource_ids[0]) == b"same"
    finally:
        catalog.close()


def test_cleanup_waits_for_put_that_has_written_blob_but_not_registered(
    tmp_path, monkeypatch
):
    """Cleanup must wait for registration before deleting an unlinked resource."""
    catalog_path = tmp_path / "dictionary.db"
    root = tmp_path / "audio"
    bootstrap = Catalog.open(catalog_path)
    bootstrap.close()
    blob_written = threading.Event()
    allow_registration = threading.Event()
    cleanup_finished = threading.Event()
    original_write_blob = AudioBlobStore._write_blob

    def paused_write_blob(self, *args):
        result = original_write_blob(self, *args)
        blob_written.set()
        assert allow_registration.wait(timeout=5)
        return result

    monkeypatch.setattr(AudioBlobStore, "_write_blob", paused_write_blob)

    def put_from_separate_connection():
        catalog = Catalog.open(catalog_path)
        try:
            return AudioBlobStore(root, catalog, max_shard_bytes=6).put(
                b"safe", "audio/mpeg", 100
            )
        finally:
            catalog.close()

    def clean_from_separate_connection():
        catalog = Catalog.open(catalog_path)
        try:
            result = AudioBlobStore(root, catalog, max_shard_bytes=6).delete_unreferenced()
            cleanup_finished.set()
            return result
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        put_future = executor.submit(put_from_separate_connection)
        assert blob_written.wait(timeout=5)
        cleanup_future = executor.submit(clean_from_separate_connection)
        assert not cleanup_finished.wait(timeout=0.2)
        allow_registration.set()
        resource_id = put_future.result(timeout=5)
        assert cleanup_future.result(timeout=5) == 1

    catalog = Catalog.open(catalog_path)
    try:
        store = AudioBlobStore(root, catalog, max_shard_bytes=6)
        assert catalog.connection.execute(
            "SELECT 1 FROM audio_resource WHERE id = ?", (resource_id,)
        ).fetchone() is None
        with pytest.raises(KeyError):
            store.read(resource_id)
    finally:
        catalog.close()


def test_cleanup_skips_snapshot_blob_registered_after_catalog_commit(
    tmp_path, monkeypatch
):
    """Deleting a snapshot row after another writer registers it loses readable audio."""
    catalog_path = tmp_path / "dictionary.db"
    root = tmp_path / "audio"
    root.mkdir()
    bootstrap = Catalog.open(catalog_path)
    try:
        shard_id = bootstrap.create_audio_shard(filename="audio-001.db", byte_size=4)
    finally:
        bootstrap.close()
    with sqlite3.connect(root / "audio-001.db") as connection:
        connection.execute(
            "CREATE TABLE audio_blob (id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, data BLOB NOT NULL)"
        )
        connection.execute(
            "INSERT INTO audio_blob (sha256, data) VALUES (?, ?)",
            (sha256(b"same").digest(), b"same"),
        )

    snapshot_taken = threading.Event()
    allow_physical_cleanup = threading.Event()
    original_delete = AudioBlobStore._delete_unregistered_blobs

    def delayed_delete(self, snapshots, failures):
        snapshot_taken.set()
        assert allow_physical_cleanup.wait(timeout=5)
        return original_delete(self, snapshots, failures)

    monkeypatch.setattr(AudioBlobStore, "_delete_unregistered_blobs", delayed_delete)

    def cleanup_from_separate_connection():
        catalog = Catalog.open(catalog_path)
        try:
            return AudioBlobStore(root, catalog, max_shard_bytes=8).delete_unreferenced()
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        cleanup = executor.submit(cleanup_from_separate_connection)
        assert snapshot_taken.wait(timeout=5)
        catalog = Catalog.open(catalog_path)
        try:
            store = AudioBlobStore(root, catalog, max_shard_bytes=8)
            resource_id = store.put(b"same", "audio/mpeg", 100)
            assert catalog.connection.execute(
                "SELECT shard_id FROM audio_resource WHERE id = ?", (resource_id,)
            ).fetchone()["shard_id"] == shard_id
        finally:
            catalog.close()
        allow_physical_cleanup.set()
        assert cleanup.result(timeout=5) == 0

    catalog = Catalog.open(catalog_path)
    try:
        assert AudioBlobStore(root, catalog, max_shard_bytes=8).read(resource_id) == b"same"
    finally:
        catalog.close()
