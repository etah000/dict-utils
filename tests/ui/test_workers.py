from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QThreadPool

from mdict_audio_app.ui.workers import TaskWorker


def test_worker_emits_result_without_blocking(qtbot):
    worker = TaskWorker(lambda progress, cancel: 42)
    with qtbot.waitSignal(worker.signals.succeeded, timeout=1000) as signal:
        QThreadPool.globalInstance().start(worker)
    assert signal.args == [42]


def test_worker_reports_progress_and_errors(qtbot):
    worker = TaskWorker(lambda progress, cancel: (progress("step"), 1 / 0)[1])
    with qtbot.waitSignals(
        [worker.signals.progress, worker.signals.failed, worker.signals.finished],
        timeout=1000,
        order="strict",
    ) as signals:
        QThreadPool.globalInstance().start(worker)
    assert signals[0].args == ["step"]
    assert "ZeroDivisionError" in signals[1].args[0]
    assert "division" in signals[1].args[0]


def test_cancel_sets_event():
    worker = TaskWorker(lambda progress, cancel: None)
    assert not worker.cancel_event.is_set()
    worker.cancel()
    assert worker.cancel_event.is_set()
