"""Qt widgets and background task helpers for the desktop application."""

from .main_window import MainWindow
from .workers import TaskWorker
from .dictionary_page import DictionaryPage, DictionaryTableModel
from .audio_page import AudioPage, AudioPreviewModel

__all__ = ["MainWindow", "TaskWorker", "DictionaryPage", "DictionaryTableModel", "AudioPage", "AudioPreviewModel"]
