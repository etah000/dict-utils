"""Two-phase MDX/MDD import orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Callable, Iterable, Protocol

from mdict_audio_app.domain.models import AudioKind, ResolutionStatus
from mdict_audio_app.parsing.base import AdapterRegistry, ParsedEntry
from mdict_audio_app.parsing.generic import normalize_sound_ref
from mdict_audio_app.storage.audio_store import AudioBlobStore
from mdict_audio_app.storage.catalog import Catalog


@dataclass(frozen=True)
class AudioMetadata:
    media_type: str
    duration_ms: int
    format_name: str = ""


class AudioProbe(Protocol):
    def probe(self, data: bytes, suffix: str) -> AudioMetadata: ...


class FFprobeAudioProbe:
    def __init__(self, ffprobe_path: str | Path = "ffprobe") -> None:
        self.ffprobe_path = str(ffprobe_path)

    def probe(self, data: bytes, suffix: str) -> AudioMetadata:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as stream:
            path = Path(stream.name)
            stream.write(data)
        try:
            result = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-show_entries", "format=duration,format_name", "-of", "json", str(path)],
                check=True, capture_output=True, text=True,
            )
            info = json.loads(result.stdout).get("format", {})
            duration_ms = max(0, round(float(info.get("duration", 0) or 0) * 1000))
            return AudioMetadata(_media_type(suffix), duration_ms, str(info.get("format_name", "")))
        finally:
            path.unlink(missing_ok=True)


@dataclass(frozen=True)
class ImportRequest:
    name: str
    mdx_path: Path
    mdd_path: Path | None = None
    source: object | None = None
    adapter_name: str | None = None
    batch_size: int = 1000


@dataclass(frozen=True)
class ImportProgress:
    phase: str
    completed: int
    total: int | None = None
    message: str = ""


@dataclass(frozen=True)
class ImportResult:
    dictionary_id: int
    entries: int = 0
    examples: int = 0
    audio_resources: int = 0
    cancelled: bool = False
    failed: bool = False
    error: str | None = None


_SUFFIXES = {".mp3", ".wav", ".ogg", ".oga", ".spx", ".opus", ".m4a", ".aac", ".flac"}
_MEDIA = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".spx": "audio/ogg", ".opus": "audio/opus", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac"}


def _media_type(suffix: str) -> str:
    return _MEDIA.get(suffix.lower(), "application/octet-stream")


class ImportService:
    def __init__(self, catalog: Catalog, audio_store: AudioBlobStore, registry: AdapterRegistry, probe: AudioProbe | None = None, operation_lock: threading.RLock | None = None) -> None:
        self.catalog, self.audio_store, self.registry = catalog, audio_store, registry
        self.probe = probe or FFprobeAudioProbe()
        self.operation_lock = operation_lock

    def run(self, request: ImportRequest, progress_callback: Callable[[ImportProgress], None] | None = None, cancel_event: threading.Event | None = None) -> ImportResult:
        if self.operation_lock is not None:
            with self.operation_lock:
                return self._run(request, progress_callback, cancel_event)
        return self._run(request, progress_callback, cancel_event)

    def _run(self, request: ImportRequest, progress_callback: Callable[[ImportProgress], None] | None = None, cancel_event: threading.Event | None = None) -> ImportResult:
        self._progress(progress_callback, "HASHING", 0, None, "\u6b63\u5728\u8ba1\u7b97\u6e90\u6587\u4ef6\u54c8\u5e0c")
        try:
            source = request.source
            if source is None:
                from .mdict_source import MDictSource
                source = MDictSource(request.mdx_path, request.mdd_path)
            dictionary_id = self._create_dictionary(request, source)
        except Exception as error:
            return ImportResult(0, failed=True, error=str(error))
        entries = examples = resources = 0
        try:
            adapter = self.registry.select(getattr(source, "metadata", {}), request.adapter_name)
            entries, examples = self._phase_one(source, adapter, dictionary_id, max(1, request.batch_size), cancel_event, progress_callback)
            if self._cancelled(cancel_event):
                self._set_status(dictionary_id, "FAILED")
                return ImportResult(dictionary_id, entries, examples, cancelled=True)
            resources = self._phase_two(source, dictionary_id, cancel_event, progress_callback)
            if self._cancelled(cancel_event):
                self._set_status(dictionary_id, "FAILED")
                return ImportResult(dictionary_id, entries, examples, resources, cancelled=True)
            self._set_status(dictionary_id, "READY")
            self._progress(progress_callback, "FINALIZING", entries, entries, "\u5bfc\u5165\u5b8c\u6210")
            return ImportResult(dictionary_id, entries, examples, resources)
        except Exception as error:
            self._set_status(dictionary_id, "FAILED")
            return ImportResult(dictionary_id, entries, examples, resources, failed=True, error=str(error))

    def _create_dictionary(self, request, source) -> int:
        return self.catalog.create_dictionary(
            name=request.name, mdx_sha256=getattr(source, "mdx_sha256", b""), source_mdx_name=request.mdx_path.name,
            source_mdd_name=request.mdd_path.name if request.mdd_path else None, mdd_sha256=getattr(source, "mdd_sha256", None),
            metadata=getattr(source, "metadata", {}), adapter_name=request.adapter_name or "auto",
        )

    def _phase_one(self, source, adapter, dictionary_id, batch_size, cancel_event, callback):
        count = examples = 0
        batch: list[tuple[str, ParsedEntry]] = []
        entries = iter(source.iter_entries())
        while not self._cancelled(cancel_event):
            try:
                headword, html = next(entries)
            except StopIteration:
                break
            batch.append((headword, adapter.parse(headword, html)))
            if len(batch) >= batch_size:
                examples += self._write_batch(dictionary_id, batch, count)
                count += len(batch)
                batch.clear()
                self._progress(callback, "PARSING_MDX", count, None, f"\u5df2\u5199\u5165 {count} \u6761\u8bcd\u6761")
        if not self._cancelled(cancel_event) and batch:
            examples += self._write_batch(dictionary_id, batch, count)
            count += len(batch)
            self._progress(callback, "PARSING_MDX", count, None, f"\u5df2\u5199\u5165 {count} \u6761\u8bcd\u6761")
        return count, examples

    def _write_batch(self, dictionary_id: int, batch: Iterable[tuple[str, ParsedEntry]], start_order: int) -> int:
        total_examples = 0
        with self.catalog.transaction():
            for offset, (headword, parsed) in enumerate(batch):
                entry_id = self.catalog.insert_entry(dictionary_id=dictionary_id, headword=headword, definition_html=parsed.definition_html, definition_text=parsed.definition_text, source_order=start_order + offset)
                example_ids = []
                for index, text in enumerate(parsed.examples):
                    cur = self.catalog.connection.execute("INSERT INTO example(entry_id, text, source_order) VALUES (?, ?, ?)", (entry_id, text, index))
                    example_ids.append(int(cur.lastrowid))
                total_examples += len(example_ids)
                for index, ref in enumerate(parsed.audio_refs):
                    kind = ref.kind
                    example_index = ref.example_index
                    if kind is AudioKind.UNKNOWN:
                        if index == 0:
                            kind = AudioKind.HEADWORD
                        elif example_ids:
                            kind = AudioKind.EXAMPLE
                            example_index = min(index - 1, len(example_ids) - 1)
                    example_id = example_ids[example_index] if example_index is not None and 0 <= example_index < len(example_ids) else None
                    self.catalog.resolve_audio_link(entry_id=entry_id, example_id=example_id, resource_id=None, kind=kind, resolution_status=ResolutionStatus.UNKNOWN, source_ref=normalize_sound_ref(ref.source_ref), source_order=index)
        return total_examples

    def _phase_two(self, source, dictionary_id, cancel_event, callback) -> int:
        links = list(self.catalog.connection.execute("SELECT id, source_ref FROM audio_link WHERE entry_id IN (SELECT id FROM entry WHERE dictionary_id = ?) AND resolution_status = 'UNKNOWN'", (dictionary_id,)))
        wanted = {str(row["source_ref"]) for row in links if row["source_ref"]}
        self._progress(callback, "SCANNING_MDD", 0, len(wanted), "\u6b63\u5728\u626b\u63cf\u5f15\u7528\u97f3\u9891")
        found: dict[str, int] = {}
        count = 0
        resources = iter(source.iter_resources(wanted))
        while not self._cancelled(cancel_event):
            try:
                path, data = next(resources)
            except StopIteration:
                break
            normalized, suffix = normalize_sound_ref(path), Path(path).suffix.lower()
            if suffix not in _SUFFIXES:
                continue
            try:
                metadata = self.probe.probe(data, suffix)
                found[normalized] = self.audio_store.put(data, metadata.media_type, metadata.duration_ms)
                count += 1
            except Exception:
                continue
        if self._cancelled(cancel_event):
            return count
        with self.catalog.transaction():
            for link in links:
                resource_id = found.get(str(link["source_ref"]))
                status = ResolutionStatus.MATCHED if resource_id is not None else ResolutionStatus.MISSING
                self.catalog.connection.execute("UPDATE audio_link SET resource_id = ?, resolution_status = ? WHERE id = ?", (resource_id, str(status), link["id"]))
        self._progress(callback, "SCANNING_MDD", len(wanted), len(wanted), "\u97f3\u9891\u94fe\u63a5\u5df2\u89e3\u6790")
        return count

    def _set_status(self, dictionary_id: int, status: str) -> None:
        with self.catalog.transaction():
            self.catalog.connection.execute("UPDATE dictionary SET status = ? WHERE id = ?", (status, dictionary_id))

    @staticmethod
    def _cancelled(event) -> bool:
        return event is not None and event.is_set()

    @staticmethod
    def _progress(callback, phase, completed, total, message):
        if callback:
            callback(ImportProgress(phase, completed, total, message))
