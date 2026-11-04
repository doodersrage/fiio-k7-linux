"""Qt plumbing shared by the tray icon and the window (PyQt6 or PySide6)."""

from __future__ import annotations

import signal
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import pyqtSignal as Signal
    BINDING = "PyQt6"
except ImportError:  # pragma: no cover - depends on what's installed
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        from PySide6.QtCore import Signal
        BINDING = "PySide6"
    except ImportError:
        raise ModuleNotFoundError("The window and tray icon need PyQt6 or PySide6 "
                                  "(Arch: sudo pacman -S python-pyqt6, Debian/Ubuntu/Fedora: python3-pyqt6).")

ICON_NAMES = ("audio-card", "audio-headphones")


def theme_icon(*names: str) -> QtGui.QIcon:
    for n in names or ICON_NAMES:
        icon = QtGui.QIcon.fromTheme(n)
        if not icon.isNull():
            return icon
    return QtGui.QIcon()


STATE_ICONS = {
    "playing": ("audio-volume-high", "audio-card"),
    "resampled": ("dialog-warning", "audio-volume-medium", "audio-card"),
    "eq": ("media-playlist-shuffle", "audio-volume-high", "audio-card"),
    "idle": ("audio-card", "audio-headphones"),
    "disconnected": ("audio-volume-muted", "audio-card"),
}


def state_icon(state: str) -> QtGui.QIcon:
    return theme_icon(*STATE_ICONS.get(state, ICON_NAMES))


class Worker(QtCore.QObject):
    """Runs slow jobs (PipeWire queries, WirePlumber restarts, downloads) one at
    a time on a background thread, so the window and tray never freeze.
    Results come back on the GUI thread."""

    _done = Signal(object, object)       # (callback, result or exception)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = 0
        self.stopped = False
        self._done.connect(self._finish)

    def _finish(self, cb, result):
        self.pending -= 1
        if self.pending == 0:
            self.busy_changed.emit(False)
        if cb and not self.stopped:
            cb(result)

    @property
    def busy(self) -> bool:
        return self.pending > 0

    def run(self, fn, on_done=None):
        """fn() runs on the worker thread; on_done(result or exception) on the GUI thread."""
        if self.stopped:
            return
        self.pending += 1
        if self.pending == 1:
            self.busy_changed.emit(True)

        def job():
            try:
                r = fn()
            except Exception as e:  # shown to the user, never fatal
                r = e
            self._done.emit(on_done, r)
        self.pool.submit(job)

    def shutdown(self):
        self.stopped = True
        self.pool.shutdown(wait=True, cancel_futures=True)


_APP = None   # keeps the QApplication alive: if Python collects it, the next widget crashes


def make_app() -> QtWidgets.QApplication:
    global _APP
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    _APP = app
    app.setApplicationName("k7ctl")
    app.setApplicationDisplayName("k7ctl")
    app.setDesktopFileName("k7ctl")
    return app


def run_app(app, cleanup) -> int:
    """Run the event loop; SIGTERM/Ctrl-C and normal exits all go through cleanup()."""
    # Qt's event loop doesn't let Python run signal handlers on its own; a
    # short heartbeat timer does, so SIGTERM/Ctrl-C quit cleanly and promptly.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: cleanup())
    heartbeat = QtCore.QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(300)
    code = app.exec()
    cleanup()
    return code
