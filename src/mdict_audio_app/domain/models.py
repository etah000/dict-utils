"""Core value objects used by the MDict audio application."""

from dataclasses import dataclass, field
from enum import StrEnum
import unicodedata


class AudioKind(StrEnum):
    HEADWORD = "HEADWORD"
    EXAMPLE = "EXAMPLE"
    UNKNOWN = "UNKNOWN"


class ResolutionStatus(StrEnum):
    MATCHED = "MATCHED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


def normalize_headword(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class AudioSelectionSettings:
    max_examples: int = 2
    max_total_ms: int | None = None
    clip_gap_ms: int = 300
    word_gap_ms: int = 800
    fallback_headword_audio: bool = True

    def __post_init__(self) -> None:
        if self.max_examples < 0:
            raise ValueError("max_examples must be non-negative")


@dataclass(frozen=True)
class AudioClip:
    word: str
    kind: AudioKind
    resource_id: int
    duration_ms: int
    dictionary_id: int
    example_text: str | None = None


@dataclass(frozen=True)
class AudioPlan:
    clips: tuple[AudioClip, ...] = field(default_factory=tuple)
    skipped: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    total_audio_ms: int = 0
    clip_gap_ms: int = 300
    word_gap_ms: int = 800

    def __post_init__(self) -> None:
        if self.clip_gap_ms < 0 or self.word_gap_ms < 0:
            raise ValueError("audio gaps must be non-negative")
