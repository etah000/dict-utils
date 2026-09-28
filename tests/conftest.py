"""Shared pytest fixtures for storage tests."""

from pathlib import Path
import sys

import pytest


SRC_ROOT = Path(__file__).parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def catalog(tmp_path):
    """Provide an isolated catalog and close its connection after each test."""
    from mdict_audio_app.storage.catalog import Catalog

    opened_catalog = Catalog.open(tmp_path / "dictionary.db")
    yield opened_catalog
    opened_catalog.close()
