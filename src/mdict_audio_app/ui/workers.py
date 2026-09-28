"""Thread-pool workers used by the Qt user interface.

Workers receive ordinary callables and never receive a SQLite connection.  A
callable may report progress by calling its first argument and should inspect
the supplied :class:`threading.Event` when doing cancellable work.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class TaskWorker(QRunnable):
    """Run ``fn(progress, cancel_event)`` on a Qt thread-pool thread."""

    def __init__(self, fn: Callable[[Callable[[object], None], threading.Event], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()
        self.cancel_event = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        """Request cancellation; the callable remains responsible for stopping."""
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.signals.progress.emit, self.cancel_event)
        except Exception as error:
            self.signals.failed.emit(
                f"{error.__class__.__name__}: {error}\n{traceback.format_exc()}"
            )
        except BaseException:
            # Do not let interpreter-level failures strand the UI's busy state.
            self.signals.failed.emit(traceback.format_exc())
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
