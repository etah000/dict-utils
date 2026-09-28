"""Build deterministic, duration-limited audio plans from word lists."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Protocol

from mdict_audio_app.domain import (
    AudioClip,
    AudioKind,
    AudioPlan,
    AudioSelectionSettings,
    normalize_headword,
)
from mdict_audio_app.services.dictionary_query import DictionaryQueryService, WordSelection


def parse_word_list(text: str) -> tuple[str, ...]:
    """Return first-seen, non-empty words, deduplicated by dictionary normalization."""

    words: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        word = line.strip()
        if not words and word.startswith("\ufeff"):
            word = word.lstrip("\ufeff").strip()
        normalized = normalize_headword(word)
        if normalized and normalized not in seen:
            seen.add(normalized)
            words.append(word)
    return tuple(words)


def parse_word_file(path: Path) -> tuple[str, ...]:
    """Read a UTF-8 text or CSV word list, taking the first CSV column."""

    suffix = path.suffix.casefold()
    if suffix == ".txt":
        return parse_word_list(path.read_text(encoding="utf-8-sig"))
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return parse_word_list("\n".join(row[0] for row in csv.reader(stream) if row))
    raise ValueError(f"unsupported word-list format: {path.suffix or '<none>'}")


class AudioProvider(Protocol):
    def resolve(self, word: str, settings: AudioSelectionSettings) -> WordSelection | None:
        """Resolve one word according to the plan's fallback settings."""


class DictionaryAudioProvider:
    """Adapter from the priority-aware dictionary query service to AudioProvider."""

    def __init__(self, query_service: DictionaryQueryService) -> None:
        self.query_service = query_service

    def resolve(self, word: str, settings: AudioSelectionSettings) -> WordSelection | None:
        return self.query_service.lookup(word, settings.fallback_headword_audio)


class AudioPlanBuilder:
    """Select headword audio followed by same-dictionary example audio."""

    def __init__(self, provider: AudioProvider) -> None:
        self.provider = provider

    def build(
        self, words: Iterable[str], settings: AudioSelectionSettings
    ) -> AudioPlan:
        clips: list[AudioClip] = []
        skipped: list[tuple[str, str]] = []
        total_ms = 0

        for word in words:
            selection = self.provider.resolve(word, settings)
            if selection is None:
                skipped.append((word, "\u7f3a\u5c11\u8bcd\u6761"))
                continue
            headword = selection.headword_audio
            if headword is None or headword.resource_id is None:
                skipped.append((word, "\u7f3a\u5c11\u8bcd\u5934\u53d1\u97f3"))
                continue

            candidates = [headword]
            example_ids = {example.id for example in selection.examples}
            example_audio_links = tuple(
                link
                for link in selection.audio_links
                if (
                    link.kind is AudioKind.EXAMPLE
                    and link.resource_id is not None
                    and link.example_id in example_ids
                )
            )
            example_audio_links = tuple(
                sorted(example_audio_links, key=lambda link: (link.source_order, link.id))
            )
            example_by_id = {}
            for link in example_audio_links:
                # Keep the first deterministic link for an example.
                example_by_id.setdefault(link.example_id, link)
            selected_examples = list(example_by_id.values())[: settings.max_examples]
            if settings.max_examples > 0 and selection.examples and not selected_examples:
                skipped.append((word, "\u7f3a\u5c11\u4f8b\u53e5\u97f3\u9891"))
            candidates.extend(selected_examples)

            duration_limited = False
            for candidate in candidates:
                gap = 0
                if clips:
                    gap = settings.clip_gap_ms if clips[-1].word == word else settings.word_gap_ms
                candidate_duration = max(0, candidate.duration_ms)
                projected = total_ms + gap + candidate_duration
                if settings.max_total_ms is not None and projected > settings.max_total_ms:
                    duration_limited = True
                    break
                clips.append(
                    AudioClip(
                        word=word,
                        kind=candidate.kind,
                        resource_id=candidate.resource_id,
                        duration_ms=candidate_duration,
                        dictionary_id=candidate.dictionary_id,
                        example_text=candidate.example_text,
                    )
                )
                total_ms = projected
            if duration_limited:
                skipped.append((word, "\u8d85\u8fc7\u6700\u5927\u603b\u65f6\u957f"))
                break

        return AudioPlan(
            clips=tuple(clips),
            skipped=tuple(skipped),
            total_audio_ms=total_ms,
            clip_gap_ms=settings.clip_gap_ms,
            word_gap_ms=settings.word_gap_ms,
        )
