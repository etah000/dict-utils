from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from mdict_audio_app.storage.catalog import CatalogFactory
from mdict_audio_app.ui.dictionary_page import DictionaryPage


def test_priority_drop_persists_order(qtbot, tmp_path):
    factory = CatalogFactory(tmp_path / "dictionary.db")
    catalog = factory.open()
    first = catalog.create_dictionary(name="first", mdx_sha256=b"1", source_mdx_name="1.mdx")
    second = catalog.create_dictionary(name="second", mdx_sha256=b"2", source_mdx_name="2.mdx")
    catalog.close()
    page = DictionaryPage({"catalog_factory": factory})
    qtbot.addWidget(page)
    page.set_priority([second, first])
    check = factory.open()
    assert check.dictionary_priorities() == [second, first]
    check.close()


def test_import_disables_button_until_worker_finishes(qtbot, tmp_path):
    fake_service = SimpleNamespace(run=lambda *args, **kwargs: [])
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db"), "import_service_factory": lambda: fake_service})
    qtbot.addWidget(page)
    page.start_import([Path("sample.mdx")])
    assert not page.import_button.isEnabled()
    page.on_import_finished()
    assert page.import_button.isEnabled()


def test_match_mdd_uses_same_stem_case_insensitively(tmp_path):
    mdx = tmp_path / "Words.MDX"
    mdd = tmp_path / "words.mdd"
    mdx.touch()
    mdd.touch()
    assert DictionaryPage.match_mdd(mdx) == mdd


def test_missing_mdd_uses_injected_picker(tmp_path):
    mdx = tmp_path / "Words.mdx"
    chosen = tmp_path / "external.mdd"
    mdx.touch()
    chosen.touch()
    page = DictionaryPage({"mdd_picker": lambda path: chosen})
    assert page.choose_mdd(mdx) == chosen


def test_import_result_failure_is_reported(qtbot, tmp_path):
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db")})
    qtbot.addWidget(page)
    failed = SimpleNamespace(failed=True, cancelled=False, error="bad source")
    page._current_import_paths = [Path("broken.mdx")]
    page._on_import_succeeded([failed])
    assert "FAILED" in page.status_label.text()
    assert "broken.mdx" in page.status_label.text()


def test_import_cancellation_is_not_reported_as_failure(qtbot, tmp_path):
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db")})
    qtbot.addWidget(page)
    cancelled = SimpleNamespace(failed=False, cancelled=True, error=None)
    page._current_import_paths = [Path("cancelled.mdx")]
    page._on_import_succeeded([cancelled])
    assert page.status_label.text() == "CANCELLED: cancelled.mdx"


def test_worker_failure_includes_source_filename(qtbot, tmp_path):
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db")})
    qtbot.addWidget(page)
    page._current_import_paths = [Path("broken.mdx")]
    page._on_import_failed("bad source")
    assert page.status_label.text() == "FAILED: broken.mdx: bad source"


def test_queued_batches_keep_their_selected_mdd(qtbot, tmp_path):
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db")})
    qtbot.addWidget(page)
    page.worker = object()
    first = (tmp_path / "first.mdx", tmp_path / "first.mdd")
    second = (tmp_path / "second.mdx", tmp_path / "second.mdd")
    page.start_import([first])
    page.start_import([second])
    assert page.pending_imports == [(first,), (second,)]


def test_cancelled_batch_is_reported_and_does_not_start_pending_work(qtbot, tmp_path):
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db")})
    qtbot.addWidget(page)
    page.worker = SimpleNamespace(cancel_event=SimpleNamespace(is_set=lambda: True), cancel=lambda: None)
    page.pending_imports = [((tmp_path / "next.mdx", None),)]
    page.cancel_import()
    assert page.pending_imports == []
    from mdict_audio_app.ui.dictionary_page import _ImportBatchResult
    page._on_import_succeeded(_ImportBatchResult((SimpleNamespace(failed=False, cancelled=False),), True))
    assert page.status_label.text() == "CANCELLED"


def test_close_waits_for_injected_thread_pool(qtbot, tmp_path):
    class Pool:
        def __init__(self):
            self.waited = None
            self.started = None
        def start(self, worker):
            self.started = worker
        def waitForDone(self, timeout):
            self.waited = timeout

    pool = Pool()
    fake_service = SimpleNamespace(run=lambda *args, **kwargs: [])
    page = DictionaryPage({"catalog_factory": CatalogFactory(tmp_path / "dictionary.db"), "thread_pool": pool, "import_service_factory": lambda: fake_service})
    qtbot.addWidget(page)
    page.start_import([tmp_path / "queued.mdx"])
    assert pool.started is page.worker
    from PySide6.QtGui import QCloseEvent
    event = QCloseEvent()
    page.closeEvent(event)
    assert pool.waited == 5000
    assert event.isAccepted()
    assert page.worker is None
    assert page._thread_pool is None
