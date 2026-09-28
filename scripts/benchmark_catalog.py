"""Synthetic catalog benchmark for the Windows offline distribution."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import tracemalloc

from mdict_audio_app.storage.audio_store import AudioBlobStore
from mdict_audio_app.storage.catalog import Catalog


def _peak_rss_bytes() -> int | None:
    """Return peak process working set on Windows without third-party modules."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if sys.platform == "linux" else 1)
    except (ImportError, AttributeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entries", type=int, default=100_000)
    parser.add_argument("--audio-resources", type=int, default=10_000)
    args = parser.parse_args()
    if args.entries < 0 or args.audio_resources < 0:
        parser.error("counts must be non-negative")
    started = time.perf_counter()
    tracemalloc.start()
    samples: list[str] = []
    hits = 0
    query_times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="mdict-benchmark-") as directory:
        catalog = Catalog.open(Path(directory) / "dictionary.db")
        try:
            store = AudioBlobStore(Path(directory) / "audio", catalog, max_shard_bytes=2 * 1024 * 1024 * 1024)
            with catalog.transaction():
                dictionary_id = catalog.create_dictionary(
                    name="synthetic", mdx_sha256=b"synthetic-mdx", source_mdx_name="synthetic.mdx"
                )
                entry_ids: list[int] = []
                for index in range(args.entries):
                    entry_ids.append(catalog.insert_entry(
                        dictionary_id=dictionary_id,
                        headword=f"word-{index}",
                        definition_html="<p>synthetic</p>",
                        definition_text="synthetic",
                        source_order=index,
                    ))
            # Write real content-addressed blobs and register links so the I/O path
            # represents the production schema, while keeping each fixture tiny.
            for index in range(args.audio_resources):
                resource_id = store.put(f"synthetic-audio-{index}".encode(), "audio/wav", 100)
                if entry_ids:
                    entry_id = entry_ids[index % len(entry_ids)]
                    with catalog.transaction():
                        catalog.resolve_audio_link(
                            entry_id=entry_id, resource_id=resource_id, kind="HEADWORD",
                            resolution_status="MATCHED", source_ref=f"audio-{index}.wav", source_order=0,
                        )
            random.seed(0)
            sample_count = max(1000, min(10_000, max(1, args.entries)))
            samples = [f"word-{random.randrange(max(1, args.entries))}" for _ in range(sample_count)]
            for word in samples:
                query_started = time.perf_counter_ns()
                found = catalog.connection.execute(
                    "SELECT 1 FROM entry WHERE normalized_headword = ? LIMIT 1", (word,)
                ).fetchone()
                query_times.append((time.perf_counter_ns() - query_started) / 1_000_000)
                hits += bool(found)
        finally:
            catalog.close()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss_bytes = _peak_rss_bytes()
    query_times.sort()
    p95 = query_times[max(0, (len(query_times) * 95 + 99) // 100 - 1)] if query_times else 0.0
    report = {"entries": args.entries, "audio_resources": args.audio_resources,
              "lookup_samples": len(samples), "lookup_hits": hits,
              "lookup_p95_ms": round(p95, 3),
              "peak_rss_bytes": peak_rss_bytes,
              "memory_source": "process-rss" if peak_rss_bytes is not None else "tracemalloc-fallback",
              "peak_traced_memory_bytes": peak_bytes,
              "elapsed_seconds": round(time.perf_counter() - started, 3)}
    print(json.dumps(report, ensure_ascii=False))
    memory_limit = peak_rss_bytes if peak_rss_bytes is not None else peak_bytes
    return 1 if memory_limit > 1 * 1024 * 1024 * 1024 or p95 > 100 else 0


if __name__ == "__main__":
    raise SystemExit(main())
