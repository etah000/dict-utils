"""Common parsing value objects and adapter selection."""

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from mdict_audio_app.domain.models import AudioKind


@dataclass(frozen=True)
class AudioReference:
    source_ref: str
    kind: AudioKind = AudioKind.UNKNOWN
    example_index: int | None = None


@dataclass(frozen=True)
class ParsedEntry:
    headword: str
    definition_html: str
    definition_text: str
    examples: tuple[str, ...] = ()
    audio_refs: tuple[AudioReference, ...] = ()


class ParserAdapter(Protocol):
    name: str

    def matches(self, metadata: Mapping[str, str]) -> bool: ...

    def parse(self, headword: str, html: str) -> ParsedEntry: ...


class AdapterRegistry:
    """Select an explicitly requested adapter, a matching adapter, or generic fallback."""

    def __init__(self, adapters: Sequence[ParserAdapter] = ()) -> None:
        self._adapters = list(adapters)

    def register(self, adapter: ParserAdapter) -> None:
        self._adapters.append(adapter)

    def select(
        self, metadata: Mapping[str, str], requested: str | None = None
    ) -> ParserAdapter:
        if requested is not None:
            for adapter in self._adapters:
                if adapter.name == requested:
                    return adapter
        for adapter in self._adapters:
            if adapter.matches(metadata):
                return adapter
        from .generic import GenericParser

        return GenericParser()
