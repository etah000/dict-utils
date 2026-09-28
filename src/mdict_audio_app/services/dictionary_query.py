"""Priority-aware reads from the dictionary catalog.

The query service deliberately selects one dictionary for textual content.  Audio
for the selected word may come from a lower-priority dictionary when explicitly
enabled, but definitions and examples are never combined across dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from mdict_audio_app.domain import AudioKind, ResolutionStatus, normalize_headword
from mdict_audio_app.storage.catalog import Catalog


@dataclass(frozen=True)
class DictionaryEntry:
    id: int
    dictionary_id: int
    headword: str
    definition_html: str
    definition_text: str
    source_order: int


@dataclass(frozen=True)
class DictionaryExample:
    id: int
    entry_id: int
    dictionary_id: int
    text: str
    source_order: int


@dataclass(frozen=True)
class ResolvedAudio:
    """A matched audio link with its dictionary and resource metadata."""

    id: int
    word: str
    kind: AudioKind
    resource_id: int
    duration_ms: int
    dictionary_id: int
    example_id: int | None
    source_ref: str
    source_order: int
    example_text: str | None = None


@dataclass(frozen=True)
class WordSelection:
    word: str
    dictionary_id: int
    entries: tuple[DictionaryEntry, ...] = field(default_factory=tuple)
    examples: tuple[DictionaryExample, ...] = field(default_factory=tuple)
    headword_audio: ResolvedAudio | None = None
    audio_links: tuple[ResolvedAudio, ...] = field(default_factory=tuple)


class DictionaryQueryService:
    """Resolve a word against enabled, successfully imported dictionaries."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def lookup(self, word: str, fallback_headword_audio: bool = True) -> WordSelection | None:
        normalized = normalize_headword(word)
        if not normalized:
            return None

        dictionaries = self._matching_dictionaries(normalized)
        if not dictionaries:
            return None
        selected_id = dictionaries[0]["dictionary_id"]
        entries = self._entries(selected_id, normalized)
        if not entries:
            return None
        entry_ids = tuple(entry.id for entry in entries)
        examples = self._examples(entry_ids)
        selected_audio = self._audio_links(entry_ids, selected_id)
        headword_audio = next(
            (link for link in selected_audio if link.kind is AudioKind.HEADWORD), None
        )

        if headword_audio is None and fallback_headword_audio:
            for candidate in dictionaries[1:]:
                candidate_entries = self._entries(candidate["dictionary_id"], normalized)
                candidate_audio = self._audio_links(
                    tuple(entry.id for entry in candidate_entries),
                    candidate["dictionary_id"],
                    kinds=(AudioKind.HEADWORD,),
                )
                if candidate_audio:
                    headword_audio = candidate_audio[0]
                    selected_audio = selected_audio + candidate_audio
                    break

        return WordSelection(
            word=word,
            dictionary_id=selected_id,
            entries=entries,
            examples=examples,
            headword_audio=headword_audio,
            audio_links=selected_audio,
        )

    def _matching_dictionaries(self, normalized: str) -> list[dict[str, int]]:
        rows = self.catalog.connection.execute(
            """
            SELECT DISTINCT d.id AS dictionary_id, d.priority
            FROM dictionary AS d
            JOIN entry AS e ON e.dictionary_id = d.id
            WHERE d.enabled = 1 AND d.status = 'READY'
              AND e.normalized_headword = ?
            ORDER BY d.priority, d.id
            """,
            (normalized,),
        )
        return [dict(row) for row in rows]

    def _entries(self, dictionary_id: int, normalized: str) -> tuple[DictionaryEntry, ...]:
        rows = self.catalog.connection.execute(
            """
            SELECT id, dictionary_id, headword, definition_html, definition_text, source_order
            FROM entry
            WHERE dictionary_id = ? AND normalized_headword = ?
            ORDER BY source_order, id
            """,
            (dictionary_id, normalized),
        )
        return tuple(DictionaryEntry(**dict(row)) for row in rows)

    def _examples(self, entry_ids: tuple[int, ...]) -> tuple[DictionaryExample, ...]:
        if not entry_ids:
            return ()
        placeholders = ",".join("?" for _ in entry_ids)
        rows = self.catalog.connection.execute(
            f"""
            SELECT x.id, x.entry_id, e.dictionary_id, x.text, x.source_order
            FROM example AS x
            JOIN entry AS e ON e.id = x.entry_id
            WHERE x.entry_id IN ({placeholders})
            ORDER BY e.source_order, x.source_order, x.id
            """,
            entry_ids,
        )
        return tuple(DictionaryExample(**dict(row)) for row in rows)

    def _audio_links(
        self,
        entry_ids: tuple[int, ...],
        dictionary_id: int,
        *,
        kinds: Iterable[AudioKind] | None = None,
    ) -> tuple[ResolvedAudio, ...]:
        if not entry_ids:
            return ()
        kind_values = tuple(str(kind) for kind in kinds) if kinds is not None else None
        placeholders = ",".join("?" for _ in entry_ids)
        kind_clause = ""
        params: list[object] = list(entry_ids)
        if kind_values:
            kind_placeholders = ",".join("?" for _ in kind_values)
            kind_clause = f" AND l.kind IN ({kind_placeholders})"
        params.extend((dictionary_id, str(ResolutionStatus.MATCHED)))
        if kind_values:
            params.extend(kind_values)
        rows = self.catalog.connection.execute(
            f"""
            SELECT l.id, e.headword AS word, l.kind, l.resource_id, r.duration_ms,
                   e.dictionary_id, l.example_id, l.source_ref, l.source_order,
                   x.text AS example_text
            FROM audio_link AS l
            JOIN entry AS e ON e.id = l.entry_id
            JOIN audio_resource AS r ON r.id = l.resource_id
            LEFT JOIN example AS x ON x.id = l.example_id AND x.entry_id = l.entry_id
            WHERE l.entry_id IN ({placeholders})
              AND e.dictionary_id = ? AND l.resolution_status = ?
              AND (l.example_id IS NULL OR x.id IS NOT NULL)
              {kind_clause}
            ORDER BY l.source_order, l.id
            """,
            params,
        )
        links: list[ResolvedAudio] = []
        for row in rows:
            values = dict(row)
            values["kind"] = AudioKind(values["kind"])
            links.append(ResolvedAudio(**values))
        return tuple(links)
