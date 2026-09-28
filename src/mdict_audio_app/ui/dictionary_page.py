"""Dictionary management page and its table model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableView, QVBoxLayout, QWidget,
)

from mdict_audio_app.importers.service import ImportRequest
from mdict_audio_app.ui.workers import TaskWorker


@dataclass(frozen=True)
class _ImportBatchResult:
    results: tuple[object, ...]
    cancelled: bool


class DictionaryTableModel(QAbstractTableModel):
    HEADERS = ("Name", "Priority", "Entries", "Audio", "Status", "Imported")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[Mapping[str, Any]] = []

    def set_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row.get("id")
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        values = (row.get("name", ""), row.get("priority", 0), row.get("entry_count", 0),
                  row.get("audio_count", 0), row.get("status", ""), row.get("imported_at", ""))
        return values[index.column()]


class DictionaryPage(QWidget):
    """Browse dictionaries, queue imports, reorder priority, and delete rows."""

    def __init__(self, services: Mapping[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = dict(services or {})
        self.model = DictionaryTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.import_button = QPushButton("Import MDX")
        self.delete_button = QPushButton("Delete")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.refresh_button = QPushButton("Refresh")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status_label = QLabel()
        self.worker: TaskWorker | None = None
        self._thread_pool = None
        self.pending_imports: list[tuple[tuple[Path, Path | None], ...]] = []
        self._current_import_paths: list[Path] = []
        self._cancel_requested = False
        self._closing = False
        self._layout()
        self.refresh()

    def _layout(self) -> None:
        buttons = QHBoxLayout()
        for button in (self.import_button, self.delete_button, self.move_up_button, self.move_down_button, self.refresh_button, self.cancel_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        self.import_button.clicked.connect(self._choose_and_import)
        self.delete_button.clicked.connect(self.delete_selected)
        self.move_up_button.clicked.connect(lambda: self.move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selected(1))
        self.refresh_button.clicked.connect(self.refresh)
        self.cancel_button.clicked.connect(self.cancel_import)

    def _open_catalog(self):
        factory = self.services.get("catalog_factory")
        opener = self.services.get("open_catalog")
        if factory is not None:
            return factory.open()
        return opener() if opener is not None else None

    def refresh(self) -> None:
        catalog = self._open_catalog()
        if catalog is None:
            self.model.set_rows([])
            return
        try:
            self.model.set_rows([dict(row) for row in catalog.dictionary_summaries()])
        finally:
            catalog.close()

    def set_priority(self, ids: Sequence[int]) -> None:
        catalog = self._open_catalog()
        try:
            catalog.set_dictionary_priorities(ids)
        finally:
            catalog.close()
        self.refresh()

    def move_selected(self, delta: int) -> None:
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        if not rows or not self.model.rows:
            return
        order = [int(row["id"]) for row in self.model.rows]
        source = rows[0] if delta < 0 else rows[-1]
        target = max(0, min(len(order) - 1, source + delta))
        if target == source:
            return
        value = order.pop(source)
        order.insert(target, value)
        self.set_priority(order)

    def selected_ids(self) -> list[int]:
        return [int(self.model.data(index, Qt.ItemDataRole.UserRole)) for index in self.table.selectionModel().selectedRows()]

    def delete_selected(self) -> None:
        if self.worker is not None:
            self.status_label.setText("Import is active; wait or cancel")
            return
        ids = self.selected_ids()
        if not ids:
            return
        catalog = self._open_catalog()
        summary = catalog.dictionary_delete_summary(ids) if catalog is not None else []
        if catalog is not None:
            catalog.close()
        counts = ", ".join(f"{row['name']}: {row['entry_count']} entries, {row['audio_count']} audio links" for row in summary)
        answer = QMessageBox.question(self, "Delete dictionaries", f"Delete selected dictionaries?\n{counts}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        callback = self.services.get("delete_dictionary")
        if callback is not None:
            self._start_delete(callback, ids)
            return
        else:
            catalog = self._open_catalog()
            try:
                for dictionary_id in ids:
                    catalog.delete_dictionary(dictionary_id)
            finally:
                catalog.close()
        self.refresh()

    def _start_delete(self, callback, ids: list[int]) -> None:
        """Run maintenance deletion off the GUI thread."""
        if self.worker is not None:
            self.status_label.setText("Import is active; wait or cancel")
            return
        self.import_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Deleting dictionaries...")

        def run(progress, cancel_event):
            if cancel_event.is_set():
                raise RuntimeError("delete cancelled")
            callback(ids)
            return True

        self.worker = TaskWorker(run)
        self.worker.signals.succeeded.connect(lambda _result: self._on_delete_succeeded())
        self.worker.signals.failed.connect(self._on_delete_failed)
        self.worker.signals.finished.connect(self._on_delete_finished)
        from PySide6.QtCore import QThreadPool
        injected_pool = self.services.get("thread_pool")
        self._thread_pool = injected_pool if injected_pool is not None else QThreadPool.globalInstance()
        self._thread_pool.start(self.worker)

    def _on_delete_succeeded(self) -> None:
        self.status_label.setText("Delete complete")
        if not self._closing:
            self.refresh()

    def _on_delete_failed(self, error: str) -> None:
        self.status_label.setText("FAILED: delete: " + str(error))

    def _on_delete_finished(self) -> None:
        self.import_button.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.worker = None
        self._thread_pool = None

    def _choose_and_import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select MDX files", "", "MDX files (*.mdx)")
        if paths:
            mdx_paths = [Path(path) for path in paths]
            batch = tuple((path, self.choose_mdd(path)) for path in mdx_paths)
            self.start_import(batch)

    @staticmethod
    def match_mdd(mdx_path: Path) -> Path | None:
        """Find a same-stem MDD beside an MDX, case-insensitively."""
        for path in mdx_path.parent.iterdir():
            if path.is_file() and path.suffix.casefold() == ".mdd" and path.stem.casefold() == mdx_path.stem.casefold():
                return path
        return None

    def choose_mdd(self, mdx_path: Path) -> Path | None:
        match = self.match_mdd(mdx_path)
        if match is not None:
            return match
        picker = self.services.get("mdd_picker")
        if picker is not None:
            selected = picker(mdx_path)
            return Path(selected) if selected else None
        selected, _ = QFileDialog.getOpenFileName(self, "Select optional MDD", str(mdx_path.parent), "MDD files (*.mdd)")
        return Path(selected) if selected else None

    def start_import(self, paths: Sequence[Path] | Sequence[tuple[Path, Path | None]]) -> None:
        raw_paths = tuple(paths)
        if not raw_paths:
            return
        if isinstance(raw_paths[0], tuple):
            batch = tuple((Path(mdx), Path(mdd) if mdd is not None else None) for mdx, mdd in raw_paths)
        else:
            batch = tuple((Path(path), self.match_mdd(Path(path))) for path in raw_paths)
        if self.worker is not None:
            self.pending_imports.append(batch)
            self.status_label.setText(f"Queued {len(self.pending_imports)} import batch(es)")
            return
        self._cancel_requested = False
        mdx_paths = [mdx for mdx, _mdd in batch]
        self._current_import_paths = mdx_paths
        self.import_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status_label.setText("Importing...")
        def run(progress, cancel_event):
            results = []
            factory = self.services.get("import_service_factory")
            if factory is None:
                raise RuntimeError("import_service_factory is not configured")
            for mdx_path, mdd_path in batch:
                if cancel_event.is_set():
                    break
                service = factory()
                request = ImportRequest(mdx_path.stem, mdx_path, mdd_path)
                try:
                    results.append(service.run(request, progress_callback=progress, cancel_event=cancel_event))
                finally:
                    catalog = getattr(service, "catalog", None)
                    close = getattr(catalog, "close", None)
                    if close is not None:
                        close()
            return _ImportBatchResult(tuple(results), cancel_event.is_set())
        self.worker = TaskWorker(run)
        self.worker.signals.progress.connect(self._on_import_progress)
        self.worker.signals.succeeded.connect(self._on_import_succeeded)
        self.worker.signals.failed.connect(self._on_import_failed)
        self.worker.signals.finished.connect(self.on_import_finished)
        from PySide6.QtCore import QThreadPool
        injected_pool = self.services.get("thread_pool")
        pool = injected_pool if injected_pool is not None else QThreadPool.globalInstance()
        self._thread_pool = pool
        pool.start(self.worker)

    def cancel_import(self) -> None:
        if self.worker is not None:
            self._cancel_requested = True
            self.pending_imports.clear()
            self.worker.cancel()

    def _on_import_progress(self, progress: object) -> None:
        completed = getattr(progress, "completed", 0)
        total = getattr(progress, "total", None)
        if total:
            self.progress.setValue(min(100, round(completed * 100 / total)))
        self.status_label.setText(getattr(progress, "message", str(progress)))

    def _on_import_succeeded(self, result: object) -> None:
        batch_cancelled = False
        if isinstance(result, _ImportBatchResult):
            results = list(result.results)
            batch_cancelled = result.cancelled
        else:
            results = result if isinstance(result, (list, tuple)) else [result]
        failures = []
        cancellations = []
        for index, item in enumerate(results):
            if getattr(item, "failed", False) or getattr(item, "cancelled", False):
                path = self._current_import_paths[min(index, len(self._current_import_paths) - 1)] if self._current_import_paths else Path("<unknown>")
                reason = getattr(item, "error", None) or ("cancelled" if getattr(item, "cancelled", False) else "unknown failure")
                if getattr(item, "cancelled", False):
                    cancellations.append(path.name)
                else:
                    failures.append(f"{path.name}: {reason}")
        if failures:
            status = "FAILED: " + "; ".join(failures)
            if cancellations:
                status += "; CANCELLED: " + ", ".join(cancellations)
        elif cancellations:
            status = "CANCELLED: " + ", ".join(cancellations)
        elif batch_cancelled or self._cancel_requested or (self.worker is not None and self.worker.cancel_event.is_set()):
            status = "CANCELLED"
        else:
            status = "Import complete"
        self.status_label.setText(status)
        if not self._closing:
            self.refresh()

    def _on_import_failed(self, error: str) -> None:
        names = ", ".join(path.name for path in self._current_import_paths) or "<unknown>"
        self.status_label.setText(f"FAILED: {names}: {error}")

    def on_import_finished(self) -> None:
        self.import_button.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.worker = None
        self._thread_pool = None
        if self.pending_imports and not self._cancel_requested and not self._closing:
            self.start_import(self.pending_imports.pop(0))

    def closeEvent(self, event) -> None:
        """Stop queued and active work before the page/window is destroyed."""
        self._closing = True
        self.pending_imports.clear()
        if self.worker is not None:
            self._cancel_requested = True
            self.worker.cancel()
            if self._thread_pool is not None:
                if self._thread_pool.waitForDone(5000) is False:
                    self.status_label.setText("CANCELLED: task is still stopping")
                    event.ignore()
                    return
            self.worker = None
            self._thread_pool = None
        event.accept()
