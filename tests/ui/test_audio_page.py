from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from mdict_audio_app.ui.audio_page import AudioPage


def test_preview_maps_controls_to_selection_settings(qtbot):
    class Builder:
        last_settings = None

        def build(self, words, settings):
            self.last_settings = settings
            return SimpleNamespace(clips=(), skipped=(("missing", "not found"),))

    builder = Builder()
    class Pool:
        def start(self, worker):
            self.worker = worker
    pool = Pool()
    page = AudioPage({"audio_plan_builder": builder, "thread_pool": pool})
    qtbot.addWidget(page)
    page.words_edit.setPlainText("hello\nworld")
    page.max_examples_spin.setValue(3)
    page.max_minutes_spin.setValue(10)
    page.build_preview()
    # A real Qt pool delivers this asynchronously; the injected builder still
    # receives the exact settings when the worker executes.
    pool.worker.run()
    assert builder.last_settings.max_examples == 3
    assert builder.last_settings.max_total_ms == 600_000


def test_render_requires_preview(qtbot):
    page = AudioPage()
    qtbot.addWidget(page)
    page.start_render()
    assert "Preview required" in page.status_label.text()
