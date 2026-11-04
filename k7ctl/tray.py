"""System tray icon: shows what the K7 is playing, plus a few quick actions."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:
    from PyQt6.QtGui import QAction, QCursor, QIcon
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
except ImportError:  # pragma: no cover - depends on what's installed
    from PySide6.QtGui import QAction, QCursor, QIcon
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import device, firmware, pipewire, rates

POLL_MS = 1000          # /proc reads: cheap
PW_POLL_EVERY = 5       # PipeWire queries (spawn processes) every N polls

AUTOSTART_NAME = "k7ctl-tray.desktop"


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def autostart_entry() -> str:
    exe = shutil.which("k7ctl") or f"{sys.executable} -m k7ctl"
    return ("[Desktop Entry]\nType=Application\nName=FiiO K7\n"
            "Comment=FiiO K7 status in the system tray\n"
            f"Exec={exe} tray\nIcon=audio-card\nX-GNOME-Autostart-enabled=true\n")


def _icon(connected: bool, playing: bool) -> QIcon:
    names = (["audio-volume-high", "audio-card"] if playing else
             ["audio-card", "audio-headphones"] if connected else
             ["audio-volume-muted", "audio-card"])
    for n in names:
        if QIcon.hasThemeIcon(n):
            return QIcon.fromTheme(n)
    return QIcon.fromTheme("audio-card")


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.tray = QSystemTrayIcon(_icon(False, False))
        self.menu = QMenu()
        self.k7: device.K7 | None = None
        self.sink: pipewire.Sink | None = None
        self.allowed: list[int] | None = None
        self.ticks = 0
        self.was_connected: bool | None = None

        self.a_status = QAction("No FiiO K7 connected")
        self.a_status.setEnabled(False)
        self.a_detail = QAction("")
        self.a_detail.setEnabled(False)
        self.a_rates = QAction("Use the music's sample rate")
        self.a_rates.setCheckable(True)
        self.a_rates.toggled.connect(self.toggle_rates)
        self.a_volume = QAction("Set PipeWire volume to 100%")
        self.a_volume.triggered.connect(self.volume_max)
        self.a_fw = QAction("Firmware…")
        self.a_fw.triggered.connect(self.show_firmware)
        self.a_auto = QAction("Start at login")
        self.a_auto.setCheckable(True)
        self.a_auto.setChecked(autostart_path().is_file())
        self.a_auto.toggled.connect(self.toggle_autostart)
        self.a_quit = QAction("Quit")
        self.a_quit.triggered.connect(app.quit)

        for a in (self.a_status, self.a_detail):
            self.menu.addAction(a)
        self.menu.addSeparator()
        for a in (self.a_rates, self.a_volume, self.a_fw):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.a_auto)
        self.menu.addAction(self.a_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh(force_pw=True)

    # ------------------------------------------------------------ refresh
    def refresh(self, force_pw: bool = False):
        self.k7 = device.find()
        connected = self.k7 is not None
        if force_pw or self.ticks % PW_POLL_EVERY == 0 or connected != self.was_connected:
            self.sink = pipewire.find_sink() if connected else None
            self.allowed = rates.live_allowed_rates()
        self.ticks += 1

        if self.was_connected is not None and connected != self.was_connected:
            self.tray.showMessage("FiiO K7", "Connected" if connected else "Disconnected",
                                  _icon(connected, False), 3000)
        self.was_connected = connected

        if not connected:
            self.a_status.setText("No FiiO K7 connected")
            self.a_detail.setVisible(False)
            self.tray.setIcon(_icon(False, False))
            self.tray.setToolTip("FiiO K7: not connected")
        else:
            s = self.k7.stream
            playing = s.state == "running"
            self.a_status.setText(f"FiiO K7: {s.describe()}")
            detail = []
            if self.sink and self.sink.volume is not None:
                detail.append(f"PipeWire volume {self.sink.volume * 100:.0f}%"
                              + (" (muted)" if self.sink.muted else ""))
            if self.k7.firmware:
                detail.append(f"firmware {self.k7.firmware}")
            self.a_detail.setText(", ".join(detail))
            self.a_detail.setVisible(bool(detail))
            self.tray.setIcon(_icon(True, playing))
            self.tray.setToolTip(f"FiiO K7: {s.describe()}")

        multi = self.allowed is not None and len(self.allowed) > 1
        self.a_rates.blockSignals(True)
        self.a_rates.setChecked(multi or rates.is_enabled())
        self.a_rates.blockSignals(False)
        self.a_volume.setEnabled(bool(self.sink and self.sink.volume is not None
                                      and (self.sink.volume < 0.999 or self.sink.muted)))

    # ------------------------------------------------------------ actions
    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.popup(QCursor.pos())

    def toggle_rates(self, on: bool):
        if on:
            rates.enable()
        else:
            rates.disable()
        self.refresh(force_pw=True)

    def volume_max(self):
        if not self.sink:
            return
        ok = QMessageBox.question(
            None, "Set volume to 100%",
            "This sets PipeWire's volume for the K7 to 100% so the DAC gets "
            "full resolution; you then control volume with the K7's knob.\n\n"
            "Turn the K7's knob down first. Continue?")
        if ok == QMessageBox.StandardButton.Yes:
            pipewire.set_full_volume(self.sink)
            self.refresh(force_pw=True)

    def show_firmware(self):
        if not self.k7:
            QMessageBox.information(None, "FiiO K7 firmware", "No FiiO K7 connected.")
            return
        text = f"Installed firmware: {self.k7.firmware or 'unknown'}\n\n{firmware.UPDATE_HELP}"
        QMessageBox.information(None, "FiiO K7 firmware", text)

    def toggle_autostart(self, on: bool):
        path = autostart_path()
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(autostart_entry())
        elif path.is_file():
            path.unlink()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("k7ctl")
    app.setDesktopFileName("k7ctl-tray")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available.", file=sys.stderr)
        return 1
    tray = Tray(app)  # noqa: F841  (kept alive for the app's lifetime)
    return app.exec()
