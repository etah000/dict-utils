"""The minimal, service-injected application shell."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from .dictionary_page import DictionaryPage
from .audio_page import AudioPage


class MainWindow(QMainWindow):
    """Desktop shell with stable navigation and page placeholders."""

    def __init__(self, services: Mapping[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = dict(services or {})
        self.setWindowTitle("MDict Audio")
        self.resize(960, 640)

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(["Dictionaries", "Words & Audio"])
        self.navigation.setFixedWidth(160)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.dictionary_page = DictionaryPage(self.services)
        self.words_audio_page = AudioPage(self.services)
        self.pages.addWidget(self.dictionary_page)
        self.pages.addWidget(self.words_audio_page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)

    @staticmethod
    def _placeholder(title: str, description: str) -> QWidget:
        page = QWidget()
        page.setObjectName(title)
        layout = QVBoxLayout(page)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignTop)
        text = QLabel(description)
        text.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addStretch(1)
        return page

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop both page workers before allowing the top-level window to close."""
        for page in (self.dictionary_page, self.words_audio_page):
            page_event = QCloseEvent()
            page.closeEvent(page_event)
            if not page_event.isAccepted():
                event.ignore()
                return
        event.accept()
