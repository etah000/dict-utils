"""Configurable CSS-selector dictionary adapter."""

import re
from typing import Mapping

from bs4 import BeautifulSoup, Tag

from mdict_audio_app.domain.models import AudioKind

from .base import AudioReference, ParsedEntry
from .generic import normalize_sound_ref


class CssSelectorAdapter:
    def __init__(
        self,
        name: str,
        title_pattern: str,
        definition_selector: str,
        example_selector: str | None = None,
        headword_audio_selector: str | None = None,
        example_audio_selector: str | None = None,
    ) -> None:
        self.name = name
        self.title_pattern = title_pattern
        self.definition_selector = definition_selector
        self.example_selector = example_selector
        self.headword_audio_selector = headword_audio_selector
        self.example_audio_selector = example_audio_selector

    def matches(self, metadata: Mapping[str, str]) -> bool:
        normalized = {str(key).casefold(): value for key, value in metadata.items()}
        title = normalized.get("title", normalized.get("name", ""))
        return re.fullmatch(self.title_pattern, title) is not None

    def parse(self, headword: str, html: str) -> ParsedEntry:
        soup = BeautifulSoup(html, "html.parser")
        definitions = soup.select(self.definition_selector)
        definition_nodes = definitions or [soup]
        definition_html = "".join(str(node) for node in definitions) or html
        definition_text = " ".join(node.get_text(" ", strip=True) for node in definition_nodes).strip()
        example_nodes = soup.select(self.example_selector) if self.example_selector else []
        examples = tuple(node.get_text(" ", strip=True) for node in example_nodes)

        refs: list[AudioReference] = []
        seen: set[tuple[str, AudioKind, int | None]] = set()
        selectors = (
            (self.headword_audio_selector, AudioKind.HEADWORD, None),
            (self.example_audio_selector, AudioKind.EXAMPLE, 0),
        )
        for selector, kind, fixed_index in selectors:
            if not selector:
                continue
            for node in soup.select(selector):
                example_index = fixed_index
                if kind is AudioKind.EXAMPLE and self.example_selector:
                    parents = [parent for parent in node.parents if isinstance(parent, Tag)]
                    for index, candidate in enumerate(example_nodes):
                        if any(parent is candidate for parent in parents):
                            example_index = index
                            break
                for value in _tag_values(node):
                    source_ref = _source_ref(value)
                    dedupe_key = (source_ref, kind, example_index)
                    if source_ref and dedupe_key not in seen:
                        seen.add(dedupe_key)
                        refs.append(AudioReference(source_ref, kind, example_index))
        return ParsedEntry(headword, definition_html, definition_text, examples, tuple(refs))


def _tag_values(tag: Tag) -> list[str]:
    values: list[str] = []
    for value in tag.attrs.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values.extend(item for item in value if isinstance(item, str))
    return values


def _source_ref(value: str) -> str:
    match = re.search(r"sound://([^\"'\s<>]+)", value, re.IGNORECASE)
    return normalize_sound_ref(match.group(1)) if match else ""
