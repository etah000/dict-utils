"""Adapter around the legacy :mod:`readmdict` MDX/MDD readers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit
import posixpath


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def normalize_resource_path(value: str) -> str:
    """Normalize an MDD key or sound URL to a safe, case-folded POSIX path."""
    if value[:8].casefold() == "sound://":
        path = unquote(value[8:]).replace("\\", "/")
        path = path.split("?", 1)[0].split("#", 1)[0]
    else:
        raw = unquote(value).replace("\\", "/")
        parsed = urlsplit(raw)
        path = parsed.path if parsed.scheme else raw.split("?", 1)[0].split("#", 1)[0]
    normalized = posixpath.normpath(path.lstrip("/"))
    if not normalized or normalized == "." or normalized.startswith("../"):
        return ""
    return normalized.casefold()


def _file_sha256(path: Path) -> bytes:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


class MDictSource:
    """Lazily expose MDX entries and the subset of MDD resources that is wanted."""

    def __init__(self, mdx_path: Path, mdd_path: Path | None = None, *, readmdict_module=None):
        if readmdict_module is None:
            import readmdict as readmdict_module
        self.mdx_path = Path(mdx_path)
        self.mdd_path = Path(mdd_path) if mdd_path is not None else None
        self._module = readmdict_module
        self._mdx = self._module.MDX(str(self.mdx_path))
        self._mdd = self._module.MDD(str(self.mdd_path)) if self.mdd_path else None
        self.metadata = self._decode_header(getattr(self._mdx, "header", {}))
        self.mdx_sha256 = _file_sha256(self.mdx_path)
        self.mdd_sha256 = _file_sha256(self.mdd_path) if self.mdd_path else None

    @staticmethod
    def _decode_header(header: Mapping[Any, Any]) -> dict[str, str]:
        return {_text(key): _text(value) for key, value in header.items()}

    def iter_entries(self) -> Iterable[tuple[str, str]]:
        for headword, definition in self._mdx.items():
            yield _text(headword), _text(definition)

    def iter_resources(self, wanted_paths: set[str]) -> Iterable[tuple[str, bytes]]:
        if self._mdd is None:
            return
        wanted = {normalize_resource_path(path) for path in wanted_paths}
        wanted.discard("")
        for raw_path, data in self._mdd.items():
            path = normalize_resource_path(_text(raw_path))
            if path in wanted:
                yield path, bytes(data)
