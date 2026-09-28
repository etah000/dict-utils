"""Application composition and command-line entry point."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading

from PySide6.QtWidgets import QApplication, QMessageBox

from mdict_audio_app.storage.audio_store import AudioBlobStore
from mdict_audio_app.storage.catalog import CatalogFactory
from mdict_audio_app.importers.service import FFprobeAudioProbe, ImportService
from mdict_audio_app.parsing.base import AdapterRegistry
from mdict_audio_app.services.audio_plan import AudioPlanBuilder, DictionaryAudioProvider
from mdict_audio_app.services.audio_render import AudioRenderer
from mdict_audio_app.services.dictionary_query import DictionaryQueryService
from mdict_audio_app.services.maintenance import MaintenanceService
from mdict_audio_app.ui.main_window import MainWindow


class _ClosablePlanBuilder:
    def __init__(self, builder: AudioPlanBuilder, catalog) -> None:
        self._builder, self._catalog = builder, catalog

    def build(self, words, settings):
        return self._builder.build(words, settings)

    def close(self) -> None:
        self._catalog.close()


class _ClosableRenderer:
    def __init__(self, renderer: AudioRenderer, catalog) -> None:
        self._renderer, self._catalog = renderer, catalog

    def render(self, *args, **kwargs):
        return self._renderer.render(*args, **kwargs)

    def close(self) -> None:
        self._catalog.close()


def create_application(data_dir: Path) -> tuple[QApplication, MainWindow]:
    """Compose the GUI and its main-thread services for an offline data dir."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    factory = CatalogFactory(data_dir / "dictionary.db")
    _recover_startup(factory, data_dir)
    operation_lock = threading.RLock()
    # Apply migrations during startup, then close this bootstrap connection;
    # pages and workers receive factories rather than a cross-thread connection.
    bootstrap_catalog = factory.open()
    bootstrap_catalog.close()
    services = {
        "catalog_factory": factory,
        # Each callable opens its own SQLite connection in its caller's thread.
        "open_catalog": factory.open,
        "open_audio_store": lambda: AudioBlobStore(
            data_dir / "audio", factory.open(), max_shard_bytes=2 * 1024 * 1024 * 1024
        ),
    }
    def import_service_factory() -> ImportService:
        catalog = factory.open()
        store = AudioBlobStore(data_dir / "audio", catalog, max_shard_bytes=2 * 1024 * 1024 * 1024)
        return ImportService(catalog, store, AdapterRegistry(), probe=FFprobeAudioProbe(_tool_path(data_dir, "ffprobe.exe", "ffprobe")), operation_lock=operation_lock)
    services["import_service_factory"] = import_service_factory

    def audio_plan_builder_factory() -> _ClosablePlanBuilder:
        catalog = factory.open()
        query = DictionaryQueryService(catalog)
        return _ClosablePlanBuilder(AudioPlanBuilder(DictionaryAudioProvider(query)), catalog)

    def audio_renderer_factory() -> _ClosableRenderer:
        catalog = factory.open()
        store = AudioBlobStore(data_dir / "audio", catalog, max_shard_bytes=2 * 1024 * 1024 * 1024)
        return _ClosableRenderer(AudioRenderer(store, ffmpeg=_tool_path(data_dir, "ffmpeg.exe", "ffmpeg")), catalog)

    services["audio_plan_builder_factory"] = audio_plan_builder_factory
    services["audio_renderer_factory"] = audio_renderer_factory
    def delete_dictionary(ids: list[int]) -> None:
        catalog = factory.open()
        store = AudioBlobStore(data_dir / "audio", catalog, max_shard_bytes=2 * 1024 * 1024 * 1024)
        try:
            if not operation_lock.acquire(blocking=False):
                raise RuntimeError("dictionary import is active; try deleting after it finishes")
            try:
                maintenance = MaintenanceService(catalog, store, data_dir)
                for dictionary_id in ids:
                    maintenance.delete_dictionary(dictionary_id)
            finally:
                operation_lock.release()
        finally:
            catalog.close()
    services["delete_dictionary"] = delete_dictionary
    app = QApplication.instance() or QApplication(sys.argv)
    return app, MainWindow(services)


def _tool_path(data_dir: Path, bundled_name: str, fallback: str) -> str:
    """Prefer data/app bundled binaries, then fall back to PATH."""
    candidates = (
        data_dir / "bin" / bundled_name,
        Path(sys.executable).resolve().parent / "bin" / bundled_name,
        Path(__file__).resolve().parents[2] / "bin" / bundled_name,
    )
    return str(next((path for path in candidates if path.is_file()), Path(fallback)))


def _recover_startup(factory: CatalogFactory, data_dir: Path) -> None:
    """Recover abandoned imports before any UI worker is allowed to start."""
    recovery_catalog = factory.open()
    try:
        recovery_store = AudioBlobStore(
            data_dir / "audio", recovery_catalog, max_shard_bytes=2 * 1024 * 1024 * 1024
        )
        MaintenanceService(recovery_catalog, recovery_store, data_dir).recover_interrupted_imports()
    finally:
        recovery_catalog.close()


def _install_excepthook(app: QApplication) -> None:
    logger = logging.getLogger("mdict_audio_app")

    def handle(exception_type, exception, tb) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, exception, tb)
            return
        logger.critical("Unhandled application exception", exc_info=(exception_type, exception, tb))
        try:
            QMessageBox.critical(None, "Application error", f"{exception_type.__name__}: {exception}")
        except Exception:
            print(f"{exception_type.__name__}: {exception}", file=sys.stderr)

    sys.excepthook = handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MDict Audio desktop application")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--self-check", action="store_true", help="check local SQLite and audio storage")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.self_check:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        factory = CatalogFactory(args.data_dir / "dictionary.db")
        _recover_startup(factory, args.data_dir)
        catalog = factory.open()
        try:
            store = AudioBlobStore(args.data_dir / "audio", catalog, max_shard_bytes=2 * 1024 * 1024 * 1024)
            report = MaintenanceService(catalog, store, args.data_dir).integrity_check()
            if report.integrity_ok:
                print("Self-check passed")
                return 0
            print("Self-check failed: " + "; ".join(report.errors), file=sys.stderr)
            return 1
        finally:
            catalog.close()
    app, window = create_application(args.data_dir)
    _install_excepthook(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
