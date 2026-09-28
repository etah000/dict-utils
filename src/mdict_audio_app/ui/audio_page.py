"""Word-list preview and audio rendering page."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTableView, QVBoxLayout, QWidget, QComboBox,
)

from mdict_audio_app.domain import AudioSelectionSettings
from mdict_audio_app.services.audio_plan import parse_word_file, parse_word_list
from mdict_audio_app.ui.workers import TaskWorker


class AudioPreviewModel(QAbstractTableModel):
    HEADERS = ("Word", "Dictionary", "Headword", "Examples", "Duration", "Skip reason")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []

    def set_plan(self, plan: Any) -> None:
        clips = list(getattr(plan, "clips", ()))
        grouped: dict[str, list[Any]] = {}
        for clip in clips:
            grouped.setdefault(clip.word, []).append(clip)
        skipped = dict(getattr(plan, "skipped", ()))
        self.beginResetModel()
        self.rows = []
        for word, items in grouped.items():
            headword = next((item for item in items if str(item.kind) == "HEADWORD"), None)
            examples = [item for item in items if str(item.kind) == "EXAMPLE"]
            duration = sum(max(0, int(item.duration_ms)) for item in items)
            self.rows.append({"word": word, "dictionary": getattr(headword or items[0], "dictionary_id", ""),
                              "headword": "yes" if headword else "no", "examples": len(examples),
                              "duration": f"{duration / 1000:.1f}s", "skip": skipped.get(word, "")})
        for word, reason in getattr(plan, "skipped", ()):
            if not any(row["word"] == word for row in self.rows):
                self.rows.append({"word": word, "dictionary": "", "headword": "no", "examples": 0,
                                  "duration": "0.0s", "skip": reason})
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
        if not index.isValid() or index.row() >= len(self.rows) or role != Qt.ItemDataRole.DisplayRole:
            return None
        return tuple(self.rows[index.row()].values())[index.column()]


class AudioPage(QWidget):
    """Collect word-list settings, preview a plan, and render it off the UI thread."""

    def __init__(self, services: Mapping[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = dict(services or {})
        self.words_edit = QPlainTextEdit()
        self.words_edit.setPlaceholderText("Paste one word per line")
        self.import_button = QPushButton("Import TXT/CSV")
        self.max_examples_spin = QSpinBox(); self.max_examples_spin.setRange(0, 100); self.max_examples_spin.setValue(2)
        self.max_minutes_spin = QSpinBox(); self.max_minutes_spin.setRange(0, 24 * 60); self.max_minutes_spin.setValue(10)
        self.clip_gap_spin = QSpinBox(); self.clip_gap_spin.setRange(0, 60_000); self.clip_gap_spin.setValue(300)
        self.word_gap_spin = QSpinBox(); self.word_gap_spin.setRange(0, 60_000); self.word_gap_spin.setValue(800)
        self.fallback_check = QPushButton("Fallback headword audio: ON"); self.fallback_check.setCheckable(True); self.fallback_check.setChecked(True)
        self.format_combo = QComboBox(); self.format_combo.addItems(["mp3", "m4a", "wav"])
        self.output_edit = QPlainTextEdit(); self.output_edit.setFixedHeight(28)
        # Stable aliases make the controls convenient for integration code.
        self.output_path_edit = self.output_edit
        self.preview_button = QPushButton("Preview")
        self.render_button = QPushButton("Generate audio"); self.render_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.setEnabled(False)
        self.status_label = QLabel()
        self.model = AudioPreviewModel(self)
        self.table = QTableView(); self.table.setObjectName("preview_table"); self.table.setModel(self.model)
        self.preview_table = self.table
        self.worker: TaskWorker | None = None
        self._thread_pool = None
        self.plan = None
        self._closing = False
        self._operation: str | None = None
        self._layout()

    def _layout(self) -> None:
        form = QFormLayout(); form.addRow("Max examples", self.max_examples_spin); form.addRow("Max minutes", self.max_minutes_spin)
        form.addRow("Clip gap (ms)", self.clip_gap_spin); form.addRow("Word gap (ms)", self.word_gap_spin)
        form.addRow("Format", self.format_combo); form.addRow("Output", self.output_edit)
        buttons = QHBoxLayout(); [buttons.addWidget(button) for button in (self.import_button, self.preview_button, self.render_button, self.cancel_button)]
        layout = QVBoxLayout(self); layout.addWidget(self.words_edit); layout.addLayout(form); layout.addWidget(self.fallback_check); layout.addLayout(buttons); layout.addWidget(self.table, 1); layout.addWidget(self.status_label)
        self.import_button.clicked.connect(self._choose_words)
        self.preview_button.clicked.connect(self.build_preview)
        self.render_button.clicked.connect(self.start_render)
        self.cancel_button.clicked.connect(self.cancel)
        self.fallback_check.toggled.connect(lambda on: self.fallback_check.setText("Fallback headword audio: " + ("ON" if on else "OFF")))

    def _settings(self) -> AudioSelectionSettings:
        minutes = self.max_minutes_spin.value()
        return AudioSelectionSettings(max_examples=self.max_examples_spin.value(), max_total_ms=minutes * 60_000 if minutes else None,
                                      clip_gap_ms=self.clip_gap_spin.value(), word_gap_ms=self.word_gap_spin.value(),
                                      fallback_headword_audio=self.fallback_check.isChecked())

    def _choose_words(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select word list", "", "Word lists (*.txt *.csv)")
        if path:
            self.words_edit.setPlainText("\n".join(parse_word_file(Path(path))))

    def _start(self, fn, success) -> None:
        if self.worker is not None:
            return
        self.preview_button.setEnabled(False); self.render_button.setEnabled(False); self.cancel_button.setEnabled(True)
        self.worker = TaskWorker(fn); self.worker.signals.succeeded.connect(success); self.worker.signals.failed.connect(self._failed); self.worker.signals.finished.connect(self._finished)
        from PySide6.QtCore import QThreadPool
        injected_pool = self.services.get("thread_pool")
        self._thread_pool = injected_pool if injected_pool is not None else QThreadPool.globalInstance()
        self._thread_pool.start(self.worker)

    def build_preview(self) -> None:
        builder = self.services.get("audio_plan_builder")
        factory = self.services.get("audio_plan_builder_factory") if builder is None else None
        if builder is None and factory is None:
            self._failed("RuntimeError: audio_plan_builder is not configured"); return
        words = parse_word_list(self.words_edit.toPlainText()); settings = self._settings()
        self.status_label.setText("Building preview...")
        self._operation = "preview"
        if factory is not None:
            self._start(lambda progress, cancel: self._run_factory(factory, "build", words, settings), self._on_preview)
        else:
            build = getattr(builder, "build", builder)
            self._start(lambda progress, cancel: build(words, settings), self._on_preview)

    def _on_preview(self, plan: Any) -> None:
        self.plan = plan; self.model.set_plan(plan); self.render_button.setEnabled(bool(getattr(plan, "clips", ())))
        self.status_label.setText(f"Preview ready: {len(getattr(plan, 'clips', ())) } clips")

    def start_render(self, output_path: Path | str | None = None) -> None:
        if isinstance(output_path, bool):
            output_path = None
        if self.plan is None:
            self.status_label.setText("Preview required before generating")
            return
        raw_path = str(output_path) if output_path is not None else self.output_edit.toPlainText().strip()
        path = Path(raw_path) if raw_path else Path("audio." + self.format_combo.currentText())
        if path.suffix.casefold() != "." + self.format_combo.currentText():
            path = path.with_suffix("." + self.format_combo.currentText())
        manifest_path = path.with_suffix(".json")
        existing = [str(item) for item in (path, manifest_path) if item.exists()]
        if existing and QMessageBox.question(
            self, "Overwrite", "Overwrite audio and manifest?\n" + "\n".join(existing)
        ) != QMessageBox.StandardButton.Yes:
            return
        renderer = self.services.get("audio_renderer")
        renderer_factory = self.services.get("audio_renderer_factory") if renderer is None else None
        if renderer is None and renderer_factory is None:
            self._failed("RuntimeError: audio_renderer is not configured"); return
        self.output_edit.setPlainText(str(path)); self.status_label.setText("Rendering...")
        self._operation = "render"
        plan = self.plan
        output_format = self.format_combo.currentText()
        if renderer_factory is not None:
            self._start(lambda progress, cancel: self._run_factory(
                renderer_factory, "render", plan, path, output_format, cancel
            ), self._on_rendered)
        else:
            self._start(lambda progress, cancel: renderer.render(plan, path, output_format, cancel), self._on_rendered)

    @staticmethod
    def _run_factory(factory, method: str, *args):
        resource = factory()
        try:
            return getattr(resource, method)(*args)
        finally:
            close = getattr(resource, "close", None)
            if close is not None:
                close()

    def _on_rendered(self, result: Any) -> None:
        self.status_label.setText(f"Generated: {getattr(result, 'output_path', result)}")
        self.render_button.setEnabled(self.plan is not None and bool(getattr(self.plan, "clips", ())))

    def _failed(self, error: str) -> None:
        self.status_label.setText("FAILED: " + str(error))
        if self._operation == "render":
            self.render_button.setEnabled(self.plan is not None and bool(getattr(self.plan, "clips", ())))

    def _finished(self) -> None:
        cancelled = self.worker is not None and self.worker.cancel_event.is_set()
        self.worker = None; self._thread_pool = None; self.preview_button.setEnabled(True); self.cancel_button.setEnabled(False)
        if cancelled:
            self.render_button.setEnabled(self.plan is not None and bool(getattr(self.plan, "clips", ())))
            self.status_label.setText("CANCELLED")
        self._operation = None

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def closeEvent(self, event) -> None:
        self._closing = True
        if self.worker is not None:
            self.worker.cancel()
            if self._thread_pool is not None:
                completed = self._thread_pool.waitForDone(5000)
                if completed is False:
                    self.status_label.setText("CANCELLED: task is still stopping")
                    event.ignore()
                    return
            self.worker = None; self._thread_pool = None
        event.accept()
