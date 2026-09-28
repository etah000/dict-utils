from pathlib import Path
import threading

from mdict_audio_app.importers.service import AudioMetadata, AudioProbe, ImportRequest, ImportService
from mdict_audio_app.parsing.base import AdapterRegistry, AudioReference, ParsedEntry
from mdict_audio_app.storage.audio_store import AudioBlobStore


class FakeSource:
    mdx_path = Path("d.mdx")
    mdd_path = Path("d.mdd")
    metadata = {"Title": "Demo"}
    mdx_sha256 = b"m" * 32
    mdd_sha256 = b"d" * 32

    def iter_entries(self):
        yield "hello", '<a href="sound://a.mp3">x</a>'

    def iter_resources(self, wanted_paths):
        yield "a.mp3", b"audio"
        yield "unused.jpg", b"image"


class FakeAdapter:
    name = "fake"

    def matches(self, metadata):
        return True

    def parse(self, headword, html):
        return ParsedEntry(headword, html, "x", ("example",), (AudioReference("a.mp3"),))


class FakeProbe(AudioProbe):
    def probe(self, data, suffix):
        return AudioMetadata("audio/mpeg", 100, "mp3")


def test_import_links_only_referenced_audio(catalog, tmp_path):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    progress = []
    result = ImportService(catalog, store, AdapterRegistry([FakeAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), mdd_path=Path("d.mdd"), source=FakeSource()),
        progress_callback=progress.append,
    )
    assert result.entries == 1
    assert result.audio_resources == 1
    assert catalog.connection.execute("SELECT status FROM dictionary").fetchone()[0] == "READY"
    assert catalog.connection.execute("SELECT COUNT(*) FROM audio_link WHERE resolution_status='MATCHED'").fetchone()[0] == 1
    assert [event.phase for event in progress] == ["HASHING", "PARSING_MDX", "SCANNING_MDD", "SCANNING_MDD", "FINALIZING"]
    assert [event.message for event in progress] == [
        "\u6b63\u5728\u8ba1\u7b97\u6e90\u6587\u4ef6\u54c8\u5e0c",
        "\u5df2\u5199\u5165 1 \u6761\u8bcd\u6761",
        "\u6b63\u5728\u626b\u63cf\u5f15\u7528\u97f3\u9891",
        "\u97f3\u9891\u94fe\u63a5\u5df2\u89e3\u6790",
        "\u5bfc\u5165\u5b8c\u6210",
    ]


def test_generic_unknown_audio_refs_get_default_headword_and_examples(catalog, tmp_path):
    class GenericAudioAdapter(FakeAdapter):
        def parse(self, headword, html):
            return ParsedEntry(
                headword, html, "x", ("example one", "example two"),
                (AudioReference("head.mp3"), AudioReference("one.mp3"), AudioReference("two.mp3")),
            )

    class GenericAudioSource(FakeSource):
        def iter_entries(self):
            yield "hello", "definition"

        def iter_resources(self, wanted_paths):
            for name in ("head.mp3", "one.mp3", "two.mp3"):
                yield name, name.encode()

    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry([GenericAudioAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), source=GenericAudioSource())
    )
    assert not result.failed
    assert [row[0:2] for row in catalog.connection.execute(
        "SELECT kind, example_id FROM audio_link ORDER BY source_order"
    )] == [("HEADWORD", None), ("EXAMPLE", 1), ("EXAMPLE", 2)]


def test_import_failure_marks_dictionary_failed(catalog, tmp_path):
    class BrokenSource(FakeSource):
        def iter_entries(self):
            raise RuntimeError("broken mdx")

    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry([FakeAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), source=BrokenSource())
    )
    assert result.failed is True
    assert catalog.connection.execute("SELECT status FROM dictionary").fetchone()[0] == "FAILED"


def test_probe_contract_is_injectable():
    assert FakeProbe().probe(b"x", ".mp3").duration_ms == 100


def test_multi_batch_entries_keep_source_order(catalog, tmp_path):
    class ManySource(FakeSource):
        def iter_entries(self):
            for index in range(3):
                yield f"word-{index}", "definition"

    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry([FakeAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), source=ManySource(), batch_size=2)
    )
    assert result.entries == 3
    assert [row[0] for row in catalog.connection.execute("SELECT source_order FROM entry ORDER BY source_order")] == [0, 1, 2]


def test_pre_cancelled_import_does_not_pull_mdx_iterator(catalog, tmp_path):
    event = threading.Event()
    event.set()

    class NoReadSource(FakeSource):
        def iter_entries(self):
            raise AssertionError("iterator was advanced after cancellation")

    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry([FakeAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), source=NoReadSource()), cancel_event=event
    )
    assert result.cancelled is True
    assert result.entries == 0


def test_cancelled_mdd_scan_keeps_unresolved_links_unknown(catalog, tmp_path):
    event = threading.Event()

    class CancelDuringMdd(FakeSource):
        def iter_resources(self, wanted_paths):
            yield "a.mp3", b"audio"
            event.set()
            yield "second.mp3", b"audio2"

    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry([FakeAdapter()]), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=Path("d.mdx"), source=CancelDuringMdd()), cancel_event=event
    )
    assert result.cancelled is True
    assert catalog.connection.execute("SELECT COUNT(*) FROM audio_link WHERE resolution_status='UNKNOWN'").fetchone()[0] == 1


def test_source_construction_failure_returns_structured_result(catalog, tmp_path):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=100)
    result = ImportService(catalog, store, AdapterRegistry(), FakeProbe()).run(
        ImportRequest(name="D", mdx_path=tmp_path / "missing.mdx")
    )
    assert result.dictionary_id == 0
    assert result.failed is True
    assert result.error
