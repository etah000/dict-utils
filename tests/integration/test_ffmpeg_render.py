from __future__ import annotations

import shutil
import subprocess

from mdict_audio_app.domain import AudioClip, AudioKind, AudioPlan
from mdict_audio_app.services.audio_render import AudioRenderer
from mdict_audio_app.storage.audio_store import AudioBlobStore

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for integration rendering",
)


def test_ffmpeg_renderer_produces_probeable_wav(tmp_path, catalog):
    sources = []
    for index, frequency in enumerate((440, 660)):
        source = tmp_path / f"source-{index}.wav"
        subprocess.run(
            [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.1", str(source)],
            check=True,
            capture_output=True,
        )
        sources.append(source)
    store = AudioBlobStore(tmp_path / "audio", catalog, max_shard_bytes=1024 * 1024)
    resource_ids = [store.put(source.read_bytes(), "audio/wav", 100) for source in sources]
    plan = AudioPlan(
        clips=(
            AudioClip("tone-a", AudioKind.HEADWORD, resource_ids[0], 100, 1),
            AudioClip("tone-b", AudioKind.HEADWORD, resource_ids[1], 100, 1),
        ),
        total_audio_ms=400,
        clip_gap_ms=0,
        word_gap_ms=200,
    )
    output = tmp_path / "tone.wav"
    AudioRenderer(store).render(plan, output, "wav")
    probe = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_name,sample_rate,channels", "-of", "json", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"sample_rate": "44100"' in probe.stdout
    assert '"channels": 2' in probe.stdout
    import json

    data = json.loads(probe.stdout)
    assert 0.35 <= float(data["format"]["duration"]) <= 0.5
