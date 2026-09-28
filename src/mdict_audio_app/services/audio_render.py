"""Render an :class:`AudioPlan` through a safe, deterministic FFmpeg pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Event
from typing import Protocol, Sequence
from uuid import uuid4

from mdict_audio_app.domain import AudioPlan
from mdict_audio_app.storage.audio_store import AudioBlobStore


class CommandRunner:
    """Run an external command without invoking a shell."""

    def run(self, args: Sequence[str]) -> None:
        subprocess.run(list(args), shell=False, check=True)


class _Runner(Protocol):
    def run(self, args: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    manifest_path: Path
    clip_count: int


class AudioRenderer:
    """Materialize BLOBs, normalize them, and concatenate them with FFmpeg."""

    _FORMATS = {
        "mp3": ("libmp3lame", "128k"),
        "m4a": ("aac", "128k"),
        "wav": ("pcm_s16le", None),
    }

    def __init__(self, store: AudioBlobStore, runner: _Runner | None = None, *, ffmpeg: str = "ffmpeg") -> None:
        self.store = store
        self.runner = runner or CommandRunner()
        self.ffmpeg = ffmpeg

    def render(
        self,
        plan: AudioPlan,
        output_path: Path,
        output_format: str | None = None,
        cancel_event: Event | None = None,
    ) -> RenderResult:
        output_path = Path(output_path)
        output_format = (output_format or output_path.suffix.removeprefix(".")).casefold()
        if output_format not in self._FORMATS or output_path.suffix.casefold() != f".{output_format}":
            raise ValueError("output_path suffix must match output_format: mp3, m4a, or wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output_path.with_suffix(".json")
        if manifest_path == output_path:
            raise ValueError("manifest path must differ from output path")
        self._check_cancel(cancel_event)

        temp_output = output_path.parent / f".{output_path.name}.{uuid4().hex}.tmp.{output_format}"
        temp_manifest = output_path.parent / f".{manifest_path.name}.{uuid4().hex}.tmp"
        backup_output = output_path.parent / f".{output_path.name}.{uuid4().hex}.bak"
        backup_manifest = output_path.parent / f".{manifest_path.name}.{uuid4().hex}.bak"
        try:
            with tempfile.TemporaryDirectory(prefix="mdict-audio-render-") as directory:
                work = Path(directory)
                items: list[Path] = []
                previous_word: str | None = None
                silence_files: dict[int, Path] = {}
                records: list[dict[str, object]] = []
                for index, clip in enumerate(plan.clips):
                    self._check_cancel(cancel_event)
                    if previous_word is not None:
                        gap_ms = plan.clip_gap_ms if previous_word == clip.word else plan.word_gap_ms
                        silence = silence_files.get(gap_ms)
                        if silence is None:
                            silence = work / f"silence-{gap_ms}.wav"
                            self._run(
                                cancel_event,
                                self.ffmpeg, "-y", "-f", "lavfi", "-i",
                                "anullsrc=channel_layout=stereo:sample_rate=44100",
                                "-t", f"{gap_ms / 1000:.3f}", "-ar", "44100", "-ac", "2",
                                "-c:a", "pcm_s16le", str(silence),
                            )
                            silence_files[gap_ms] = silence
                        items.append(silence)

                    suffix = self._suffix_for_resource(clip.resource_id)
                    source = work / f"clip-{index:06d}{suffix}"
                    source.write_bytes(self.store.read(clip.resource_id))
                    normalized = work / f"normalized-{index:06d}.wav"
                    self._run(
                        cancel_event, self.ffmpeg, "-y", "-i", str(source),
                        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(normalized),
                    )
                    items.append(normalized)
                    records.append({
                        "index": index,
                        "word": clip.word,
                        "kind": str(clip.kind),
                        "resource_id": clip.resource_id,
                        "duration_ms": clip.duration_ms,
                        "dictionary_id": clip.dictionary_id,
                        "example_text": clip.example_text,
                    })
                    previous_word = clip.word

                concat = work / "concat.txt"
                concat.write_text("\n".join(self._concat_line(path) for path in items) + "\n", encoding="utf-8")
                codec, bitrate = self._FORMATS[output_format]
                args = [self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c:a", codec]
                if bitrate is not None:
                    args.extend(["-b:a", bitrate])
                args.append(str(temp_output))
                self._run(cancel_event, *args)
                manifest = {
                    "output_format": output_format,
                    "total_audio_ms": plan.total_audio_ms,
                    "clip_gap_ms": plan.clip_gap_ms,
                    "word_gap_ms": plan.word_gap_ms,
                    "clips": records,
                    "skipped": [list(item) for item in plan.skipped],
                }
                temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._check_cancel(cancel_event)
            self._publish_pair(
                temp_output,
                output_path,
                temp_manifest,
                manifest_path,
                backup_output,
                backup_manifest,
            )
            return RenderResult(output_path, manifest_path, len(plan.clips))
        finally:
            for path in (temp_output, temp_manifest):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _publish_pair(
        temp_output: Path,
        output_path: Path,
        temp_manifest: Path,
        manifest_path: Path,
        backup_output: Path,
        backup_manifest: Path,
    ) -> None:
        """Publish audio and manifest together, restoring both on any failure."""
        had_output = output_path.exists()
        had_manifest = manifest_path.exists()
        moved_output = False
        moved_manifest = False
        published_output = False
        published_manifest = False
        try:
            if had_output:
                output_path.replace(backup_output)
                moved_output = True
            if had_manifest:
                manifest_path.replace(backup_manifest)
                moved_manifest = True
            temp_output.replace(output_path)
            published_output = True
            temp_manifest.replace(manifest_path)
            published_manifest = True
        except BaseException:
            if published_output:
                output_path.unlink(missing_ok=True)
            if published_manifest:
                manifest_path.unlink(missing_ok=True)
            if moved_output and backup_output.exists():
                try:
                    backup_output.replace(output_path)
                except OSError:
                    pass
            if moved_manifest and backup_manifest.exists():
                try:
                    backup_manifest.replace(manifest_path)
                except OSError:
                    pass
            raise
        else:
            # Old files are no longer needed once both new files are visible.
            backup_output.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)

    def _run(self, cancel_event: Event | None, *args: str) -> None:
        self._check_cancel(cancel_event)
        self.runner.run(args)

    @staticmethod
    def _check_cancel(cancel_event: Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("render cancelled")

    def _suffix_for_resource(self, resource_id: int) -> str:
        row = self.store.catalog.connection.execute(
            "SELECT media_type FROM audio_resource WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            return ".bin"
        media_type = str(row[0]).split(";", 1)[0].casefold()
        return {"audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg"}.get(media_type, ".bin")

    @staticmethod
    def _concat_line(path: Path) -> str:
        escaped = path.as_posix().replace("'", "'\\''")
        return f"file '{escaped}'"
