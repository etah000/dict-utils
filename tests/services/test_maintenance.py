from pathlib import Path

from mdict_audio_app.services.maintenance import MaintenanceService
from mdict_audio_app.storage.audio_store import AudioBlobStore


def _service(catalog, tmp_path: Path):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    return MaintenanceService(catalog, store, tmp_path), store


def test_backup_contains_catalog_and_every_registered_shard(catalog, tmp_path):
    maintenance, store = _service(catalog, tmp_path)
    store.put(b"synthetic-audio", "audio/wav", 100)
    destination = tmp_path / "backup"
    report = maintenance.backup(destination)
    assert (destination / "dictionary.db").is_file()
    assert {p.name for p in destination.glob("audio-*.db")} == set(report.shard_files)
    assert (destination / "integrity.json").is_file()
    assert report.integrity_ok


def test_integrity_reports_missing_shard(catalog, tmp_path):
    maintenance, store = _service(catalog, tmp_path)
    store.put(b"audio", "audio/wav", 1)
    shard = next((tmp_path / "audio").glob("audio-*.db"))
    shard.unlink()
    report = maintenance.integrity_check()
    assert not report.integrity_ok
    assert "missing audio shard" in report.errors[0]


def test_recover_marks_importing_failed_and_removes_work_dir(catalog, tmp_path):
    maintenance, _ = _service(catalog, tmp_path)
    with catalog.transaction():
        dictionary_id = catalog.create_dictionary(
            name="interrupted", mdx_sha256=b"interrupted", source_mdx_name="x.mdx"
        )
    stale = tmp_path / ".mdict-import-123"
    stale.mkdir()
    (stale / "partial").write_bytes(b"x")
    assert maintenance.recover_interrupted_imports() == 1
    assert catalog.connection.execute("SELECT status FROM dictionary WHERE id = ?", (dictionary_id,)).fetchone()[0] == "FAILED"
    assert not stale.exists()


def test_delete_dictionary_collects_unreferenced_audio(catalog, tmp_path):
    maintenance, store = _service(catalog, tmp_path)
    with catalog.transaction():
        dictionary_id = catalog.create_dictionary(
            name="delete", mdx_sha256=b"delete", source_mdx_name="x.mdx", status="READY"
        )
    store.put(b"orphan", "audio/wav", 1)
    assert maintenance.delete_dictionary(dictionary_id) == 1
