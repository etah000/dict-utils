from pathlib import Path

import pytest

from mdict_audio_app.domain import AudioSelectionSettings
from mdict_audio_app.services.audio_plan import AudioPlanBuilder, DictionaryAudioProvider
from mdict_audio_app.services.dictionary_query import DictionaryQueryService
from mdict_audio_app.storage.audio_store import AudioBlobStore


def test_startup_recovery_marks_import_failed_and_removes_temp(tmp_path: Path):
    pytest.importorskip("PySide6")
    from mdict_audio_app.main import _recover_startup
    from mdict_audio_app.storage.catalog import CatalogFactory

    factory = CatalogFactory(tmp_path / "dictionary.db")
    catalog = factory.open()
    try:
        with catalog.transaction():
            dictionary_id = catalog.create_dictionary(
                name="interrupted", mdx_sha256=b"startup", source_mdx_name="startup.mdx"
            )
    finally:
        catalog.close()
    stale = tmp_path / ".mdict-import-startup"
    stale.mkdir()
    (stale / "partial").write_bytes(b"partial")

    _recover_startup(factory, tmp_path)

    checked = factory.open()
    try:
        assert checked.connection.execute(
            "SELECT status FROM dictionary WHERE id = ?", (dictionary_id,)
        ).fetchone()[0] == "FAILED"
    finally:
        checked.close()
    assert not stale.exists()


def test_synthetic_dictionary_to_audio_plan(catalog, tmp_path: Path):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    headword_id = store.put(b"headword", "audio/wav", 100)
    example_id = store.put(b"example", "audio/wav", 120)
    with catalog.transaction():
        dictionary_id = catalog.create_dictionary(
            name="synthetic", mdx_sha256=b"synthetic", source_mdx_name="synthetic.mdx", status="READY"
        )
        entry_id = catalog.insert_entry(
            dictionary_id=dictionary_id, headword="alpha", definition_html="<p>alpha</p>",
            definition_text="alpha", source_order=0,
        )
        example_row = catalog.connection.execute(
            "INSERT INTO example(entry_id, text, source_order) VALUES (?, ?, ?)",
            (entry_id, "an alpha example", 0),
        )
        example_row_id = int(example_row.lastrowid)
        catalog.resolve_audio_link(entry_id=entry_id, resource_id=headword_id, kind="HEADWORD",
                                   resolution_status="MATCHED", source_ref="head.wav", source_order=0)
        catalog.resolve_audio_link(entry_id=entry_id, example_id=example_row_id, resource_id=example_id,
                                   kind="EXAMPLE", resolution_status="MATCHED", source_ref="example.wav", source_order=1)
    query = DictionaryQueryService(catalog)
    plan = AudioPlanBuilder(DictionaryAudioProvider(query)).build(
        ["alpha"], AudioSelectionSettings(max_examples=1, clip_gap_ms=0, word_gap_ms=0)
    )
    assert [clip.resource_id for clip in plan.clips] == [headword_id, example_id]
    assert plan.total_audio_ms == 220
