"""Generic extraction for MDict HTML with sound:// references."""

import posixpath
import re
from typing import Mapping
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from mdict_audio_app.domain.models import AudioKind

from .base import AudioReference, ParsedEntry


_SOUND_RE = re.compile(r"sound://[^\"'\s<>]+", re.IGNORECASE)


def normalize_sound_ref(value: str) -> str:
    """Return the stable, case-insensitive path used to resolve an MDD resource."""
    raw = unquote(value).replace("\\", "/")
    parsed = urlsplit(raw)
    path = parsed.path if parsed.scheme else raw.split("?", 1)[0].split("#", 1)[0]
    path = path.lstrip("/")
    normalized = posixpath.normpath(path)
    if normalized == "." or normalized.startswith("../"):
        return ""
    return normalized.casefold()


def _sound_refs(value: str) -> list[str]:
    # An attribute can contain fallback markup or multiple URLs; one source
    # attribute represents one dictionary resource for import purposes.
    for match in _SOUND_RE.finditer(value):
        normalized = normalize_sound_ref(match.group()[8:])
        if normalized:
            return [normalized]
    return []


class GenericParser:
    name = "generic"

    def matches(self, metadata: Mapping[str, str]) -> bool:
        return False

    def parse(self, headword: str, html: str) -> ParsedEntry:
        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()
        refs: list[AudioReference] = []
        for tag in soup.find_all(True):
            for value in tag.attrs.values():
                values = value if isinstance(value, (list, tuple)) else (value,)
                for item in values:
                    if not isinstance(item, str):
                        continue
                    for source_ref in _sound_refs(item):
                        if source_ref not in seen:
                            seen.add(source_ref)
                            refs.append(AudioReference(source_ref))
        text = soup.get_text(" ", strip=True)
        examples = tuple(node.get_text(" ", strip=True) for node in soup.find_all("li"))
        return ParsedEntry(headword, html, text, examples, tuple(refs))
