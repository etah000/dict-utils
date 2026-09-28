"""HTML parsing adapters for dictionary entries."""

from .base import AdapterRegistry, AudioReference, ParsedEntry, ParserAdapter
from .css_selector import CssSelectorAdapter
from .generic import GenericParser

__all__ = [
    "AdapterRegistry",
    "AudioReference",
    "CssSelectorAdapter",
    "GenericParser",
    "ParsedEntry",
    "ParserAdapter",
]
