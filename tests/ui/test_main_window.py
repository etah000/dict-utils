from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("PySide6")

from mdict_audio_app.ui.main_window import MainWindow
from mdict_audio_app.main import create_application


def test_main_window_has_two_navigable_placeholder_pages(qtbot):
    window = MainWindow({"marker": object()})
    qtbot.addWidget(window)
    assert window.navigation.count() == 2
    assert window.pages.count() == 2
    assert window.pages.currentWidget() is window.dictionary_page
    window.navigation.setCurrentRow(1)
    assert window.pages.currentWidget() is window.words_audio_page
    assert window.services["marker"] is not None


def test_application_injects_factories_not_shared_sqlite_connection(tmp_path, qtbot):
    app, window = create_application(tmp_path)
    qtbot.addWidget(window)
    assert (tmp_path / "dictionary.db").exists()
    with sqlite3.connect(tmp_path / "dictionary.db") as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone() == (1,)
    assert "catalog" not in window.services
    first = window.services["open_catalog"]()
    second = window.services["open_catalog"]()
    assert first.connection is not second.connection
    first.close()
    second.close()
    app.quit()


def test_application_injects_thread_local_audio_factories(tmp_path, qtbot):
    app, window = create_application(tmp_path)
    qtbot.addWidget(window)
    builder = window.services["audio_plan_builder_factory"]()
    renderer = window.services["audio_renderer_factory"]()
    assert callable(getattr(builder, "build"))
    assert callable(getattr(renderer, "render"))
    builder.close()
    renderer.close()
    app.quit()
