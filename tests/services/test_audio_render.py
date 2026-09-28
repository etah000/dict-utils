from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mdict_audio_app.domain import AudioClip, AudioKind, AudioPlan
from mdict_audio_app.services.audio_render import AudioRenderer, CommandRunner
from mdict_audio_app.storage.audio_store import AudioBlobStore


class FakeRunner:
    def __init__(self, *, fail=False):
        self.commands: list[tuple[str, ...]] = []
        self.fail = fail

    def run(self, args):
        command = tuple(str(value) for value in args)
        self.commands.append(command)
        if self.fail:
            raise RuntimeError("ffmpeg failed")
        output = Path(command[-1])
        if output.suffix in {".wav", ".mp3", ".m4a"}:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"rendered")


def _plan(store: AudioBlobStore) -> AudioPlan:
    resource_id = store.put("你好".encode("utf-8"), "audio/mpeg", 120)
    return AudioPlan(
        clips=(AudioClip("你好", AudioKind.HEADWORD, resource_id, 120, 1),),
        total_audio_ms=120,
    )


def test_render_materializes_blobs_and_writes_manifest(tmp_path, catalog):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    runner = FakeRunner()
    output = tmp_path / "lesson.mp3"
    result = AudioRenderer(store, runner, ffmpeg="ffmpeg").render(
        _plan(store), output, "mp3"
    )

    assert result.output_path == output
    assert '"word": "你好"' in result.manifest_path.read_text("utf-8")
    assert any("-ar" in command and "44100" in command for command in runner.commands)
    assert any("-ac" in command and "2" in command for command in runner.commands)
    assert output.read_bytes() == b"rendered"


def test_command_runner_never_uses_shell(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("subprocess.run", fake_run)
    CommandRunner().run(("ffmpeg", "-i", "input file.wav"))
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["check"] is True


def test_render_preserves_existing_output_on_failure(tmp_path, catalog):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    output = tmp_path / "lesson.wav"
    output.write_bytes(b"old")
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        AudioRenderer(store, FakeRunner(fail=True)).render(_plan(store), output, "wav")
    assert output.read_bytes() == b"old"
    assert not list(tmp_path.glob(".lesson.wav.*"))


def test_render_honors_cancellation_before_command(tmp_path, catalog):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    event = threading.Event()
    event.set()
    runner = FakeRunner()
    with pytest.raises(RuntimeError, match="cancel"):
        AudioRenderer(store, runner).render(_plan(store), tmp_path / "out.wav", "wav", event)
    assert runner.commands == []


def test_manifest_publish_failure_restores_existing_audio_and_manifest(tmp_path, catalog, monkeypatch):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    output = tmp_path / "out.wav"
    manifest = tmp_path / "out.json"
    output.write_bytes(b"old audio")
    manifest.write_text('{"old": true}\n', encoding="utf-8")
    original_replace = Path.replace
    failed = False

    def fail_manifest_replace(self, target):
        nonlocal failed
        if Path(target) == manifest and ".tmp" in self.name and not failed:
            failed = True
            raise OSError("manifest publish failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="manifest publish failed"):
        AudioRenderer(store, FakeRunner()).render(_plan(store), output, "wav")
    assert output.read_bytes() == b"old audio"
    assert manifest.read_text("utf-8") == '{"old": true}\n'
    assert not list(tmp_path.glob(".out.*.bak"))


def test_render_rejects_output_format_suffix_mismatch(tmp_path, catalog):
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024)
    with pytest.raises(ValueError, match="suffix"):
        AudioRenderer(store, FakeRunner()).render(_plan(store), tmp_path / "lesson.mp3", "wav")
