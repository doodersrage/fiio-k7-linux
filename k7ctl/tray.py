"""System tray icon: what the K7 is playing, plus quick settings."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QAction, QActionGroup, QCursor, QIcon
    from PyQt6.QtWidgets import (QApplication, QInputDialog, QMenu, QMessageBox,
                                 QSystemTrayIcon)
except ImportError:  # pragma: no cover - depends on what's installed
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
    from PySide6.QtWidgets import (QApplication, QInputDialog, QMenu, QMessageBox,
                                   QSystemTrayIcon)

from . import eq, firmware, keepawake, latency, pipewire, rates, snapshot
from .device import format_rate

POLL_MS = 2000

AUTOSTART_NAME = "k7ctl-tray.desktop"


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def autostart_entry() -> str:
    exe = shutil.which("k7ctl") or f"{sys.executable} -m k7ctl"
    return ("[Desktop Entry]\nType=Application\nName=FiiO K7\n"
            "Comment=FiiO K7 status in the system tray\n"
            f"Exec={exe} tray\nIcon=audio-card\nX-GNOME-Autostart-enabled=true\n")


def _icon(state: str) -> QIcon:
    names = {
        "playing": ["audio-volume-high", "audio-card"],
        "resampled": ["dialog-warning", "audio-volume-medium", "audio-card"],
        "eq": ["media-playlist-shuffle", "audio-volume-high", "audio-card"],
        "idle": ["audio-card", "audio-headphones"],
        "disconnected": ["audio-volume-muted", "audio-card"],
    }.get(state, ["audio-card"])
    for n in names:
        if QIcon.hasThemeIcon(n):
            return QIcon.fromTheme(n)
    return QIcon.fromTheme("audio-card")


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.tray = QSystemTrayIcon(_icon("disconnected"))
        self.snap: snapshot.Snapshot | None = None
        self.was_connected: bool | None = None
        self.menu = QMenu()

        self.a_status = self._info("No FiiO K7 connected")
        self.a_detail = self._info("")
        self.a_warn = self._info("")
        self.menu.addSeparator()

        self.a_rates = self._check("Use the music's sample rate", self.toggle_rates)
        self.a_awake = self._check("Keep the K7 awake (no cut-off sounds)", self.toggle_keepawake)
        self.a_volume = QAction("Set PipeWire volume to 100%")
        self.a_volume.triggered.connect(self.volume_max)
        self.menu.addAction(self.a_volume)

        # EQ submenu, rebuilt when opened
        self.m_eq = self.menu.addMenu("Headphone EQ")
        self.m_eq.aboutToShow.connect(self.build_eq_menu)

        # latency submenu
        self.m_lat = self.menu.addMenu("Latency")
        self.g_lat = QActionGroup(self.m_lat)
        self.lat_actions = {}
        for name, (v, desc) in latency.PRESETS.items():
            a = QAction(f"{name} ({v[0]} samples)", self.m_lat)
            a.setToolTip(desc)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, n=name: self.set_latency(n))
            self.g_lat.addAction(a)
            self.m_lat.addAction(a)
            self.lat_actions[name] = a

        self.menu.addSeparator()
        a_fw = QAction("Firmware…", self.menu)
        a_fw.triggered.connect(self.show_firmware)
        self.menu.addAction(a_fw)
        self.a_auto = self._check("Start at login", self.toggle_autostart)
        self.a_auto.setChecked(autostart_path().is_file())
        a_quit = QAction("Quit", self.menu)
        a_quit.triggered.connect(app.quit)
        self.menu.addAction(a_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()

    def _info(self, text: str) -> QAction:
        a = QAction(text, self.menu)
        a.setEnabled(False)
        self.menu.addAction(a)
        return a

    def _check(self, text: str, slot) -> QAction:
        a = QAction(text, self.menu)
        a.setCheckable(True)
        a.toggled.connect(slot)
        self.menu.addAction(a)
        return a

    @staticmethod
    def _set_checked(a: QAction, on: bool):
        a.blockSignals(True)
        a.setChecked(on)
        a.blockSignals(False)

    # ------------------------------------------------------------ refresh
    def refresh(self):
        s = self.snap = snapshot.take()
        connected = s.connected
        if self.was_connected is not None and connected != self.was_connected:
            self.tray.showMessage("FiiO K7", "Connected" if connected else "Disconnected",
                                  _icon("idle" if connected else "disconnected"), 3000)
        self.was_connected = connected

        state = s.css_class()
        self.tray.setIcon(_icon(state))
        if not connected:
            self.a_status.setText("No FiiO K7 connected")
            self.a_detail.setVisible(False)
            self.a_warn.setVisible(False)
            self.tray.setToolTip("FiiO K7: not connected")
        else:
            desc = s.k7.stream.describe()
            self.a_status.setText(f"FiiO K7: {desc}")
            detail = []
            if s.eq_on:
                detail.append(f"EQ: {s.eq_profile}")
            elif s.bit_perfect:
                detail.append("bit-perfect")
            if s.sink and s.sink.volume is not None:
                detail.append(f"volume {s.sink.volume * 100:.0f}%" + (" (muted)" if s.sink.muted else ""))
            self.a_detail.setText(", ".join(detail))
            self.a_detail.setVisible(bool(detail))
            why = s.why_resampled()
            self.a_warn.setText(f"⚠ {why[0]}" if why else "")
            self.a_warn.setVisible(bool(why))
            tip = [f"FiiO K7: {desc}"] + ([f"EQ: {s.eq_profile}"] if s.eq_on else []) + why
            self.tray.setToolTip("\n".join(tip))

        self._set_checked(self.a_rates, (s.allowed_rates is not None and len(s.allowed_rates) > 1)
                          or rates.is_enabled())
        self._set_checked(self.a_awake, bool(s.keepawake) or s.keepawake_config)
        self.a_volume.setEnabled(bool(s.sink and s.sink.volume is not None
                                      and (s.sink.volume < 0.999 or s.sink.muted)))
        for name, a in self.lat_actions.items():
            a.setChecked(name == s.latency)

    # ------------------------------------------------------------ menus
    def build_eq_menu(self):
        m = self.m_eq
        m.clear()
        on = bool(self.snap and self.snap.eq_on)
        sel = eq.selected_profile()
        g = QActionGroup(m)
        off = QAction("Off (bit-perfect)", m)
        off.setCheckable(True)
        off.setChecked(not on)
        off.triggered.connect(self.eq_off)
        g.addAction(off)
        m.addAction(off)
        for name in eq.list_profiles():
            a = QAction(name, m)
            a.setCheckable(True)
            a.setChecked(on and name == sel)
            a.triggered.connect(lambda _=False, n=name: self.eq_on(n))
            g.addAction(a)
            m.addAction(a)
        m.addSeparator()
        find = QAction("Find my headphones…", m)
        find.triggered.connect(self.eq_find)
        m.addAction(find)

    # ------------------------------------------------------------ actions
    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.popup(QCursor.pos())

    def _error(self, title: str, err: Exception):
        QMessageBox.warning(None, title, str(err))

    def toggle_rates(self, on: bool):
        rates.enable() if on else rates.disable()
        self.refresh()

    def toggle_keepawake(self, on: bool):
        keepawake.enable() if on else keepawake.disable()
        self.refresh()

    def set_latency(self, name: str):
        latency.apply(name)
        self.refresh()

    def eq_on(self, name: str):
        try:
            eq.enable(name)
        except eq.EqError as e:
            self._error("Headphone EQ", e)
        self.refresh()

    def eq_off(self):
        eq.disable()
        self.refresh()

    def eq_find(self):
        query, ok = QInputDialog.getText(None, "Headphone EQ",
                                         "Headphone model (searches AutoEQ):")
        if not ok or not query.strip():
            return
        try:
            hits = eq.search(query)
        except eq.EqError as e:
            return self._error("Headphone EQ", e)
        if not hits:
            QMessageBox.information(None, "Headphone EQ", f"No AutoEQ profiles match “{query}”.")
            return
        labels = [f"{h.name}  [{h.source}]" for h in hits[:200]]
        choice, ok = QInputDialog.getItem(None, "Headphone EQ", "Pick a profile:", labels, 0, False)
        if not ok:
            return
        entry = hits[labels.index(choice)]
        try:
            name, _ = eq.download(entry)
            eq.enable(name)
        except eq.EqError as e:
            return self._error("Headphone EQ", e)
        self.tray.showMessage("FiiO K7", f"EQ on: {name}", _icon("eq"), 3000)
        self.refresh()

    def volume_max(self):
        sink = self.snap.sink if self.snap else None
        if not sink:
            return
        ok = QMessageBox.question(
            None, "Set volume to 100%",
            "This sets PipeWire's volume for the K7 to 100% so the DAC gets "
            "full resolution; you then control volume with the K7's knob.\n\n"
            "Turn the K7's knob down first. Continue?")
        if ok == QMessageBox.StandardButton.Yes:
            pipewire.set_full_volume(sink)
            self.refresh()

    def show_firmware(self):
        k7 = self.snap.k7 if self.snap else None
        if not k7:
            QMessageBox.information(None, "FiiO K7 firmware", "No FiiO K7 connected.")
            return
        rates_txt = ", ".join(format_rate(r) for r in k7.supported_rates)
        text = (f"Installed firmware: {k7.firmware or 'unknown'}\n"
                f"Sample rates: {rates_txt}\n\n{firmware.UPDATE_HELP}")
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
