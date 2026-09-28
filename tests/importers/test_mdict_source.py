from pathlib import Path

from mdict_audio_app.importers.mdict_source import MDictSource, normalize_resource_path


class FakeMDX:
    def __init__(self, path):
        self.header = {b"Title": b"Demo", b"Encoding": b"UTF-8"}

    def items(self):
        return iter([(b"Hello", "<b>world</b>".encode()), (b"Cafe", b"coffee")])


class FakeMDD:
    def __init__(self, path):
        self.header = {b"Encoding": b"UTF-8"}

    def items(self):
        return iter([(b"\\sound\\a.mp3", b"mp3"), (b"b.jpg", b"jpg")])


class FakeReadmdict:
    MDX = FakeMDX
    MDD = FakeMDD


def test_source_decodes_entries_filters_and_hashes(tmp_path: Path):
    mdx = tmp_path / "demo.mdx"
    mdd = tmp_path / "demo.mdd"
    mdx.write_bytes(b"mdx")
    mdd.write_bytes(b"mdd")
    source = MDictSource(mdx, mdd, readmdict_module=FakeReadmdict)

    assert list(source.iter_entries()) == [("Hello", "<b>world</b>"), ("Cafe", "coffee")]
    assert list(source.iter_resources({"sound/a.mp3"})) == [("sound/a.mp3", b"mp3")]
    assert source.metadata["Title"] == "Demo"
    assert len(source.mdx_sha256) == 32
    assert len(source.mdd_sha256) == 32


def test_normalize_resource_path_handles_url_encoding_and_traversal():
    assert normalize_resource_path("sound://%5Csound%5Ca.MP3") == "sound/a.mp3"
    assert normalize_resource_path("../secret.mp3") == ""
