"""System tray icon: what the K7 is playing, quick settings, and the window.

Left-click opens the window; the right-click menu has the status and the
common settings. Slow work runs on the shared Worker thread (see qt.py).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import device, eq, keepawake, latency, rates, settings, snapshot
from .device import format_rate
from .qt import QtCore, QtGui, QtWidgets, Worker, make_app, run_app, state_icon

W = QtWidgets

POLL_MS = 3000
AUTOSTART_NAME = "k7ctl-tray.desktop"


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def autostart_entry() -> str:
    exe = shutil.which("k7ctl") or f"{sys.executable} -m k7ctl"
    return ("[Desktop Entry]\nType=Application\nName=FiiO K7\n"
            "Comment=FiiO K7 status in the system tray\n"
            f"Exec={exe} tray\nIcon=audio-card\nX-GNOME-Autostart-enabled=true\n")


class Tray:
    def __init__(self, app: W.QApplication, worker: Worker | None = None):
        self.app = app
        self.worker = worker or Worker()
        self.window = None
        self.snap: snapshot.Snapshot | None = None
        self.was_connected: bool | None = None
        self.polling = False
        self.last_rate: str | None = None
        self.last_resampled: set[str] = set()

        self.tray = W.QSystemTrayIcon(state_icon("disconnected"))
        self.menu = W.QMenu()

        a_open = QtGui.QAction("Open k7ctl…", self.menu)
        a_open.triggered.connect(self.open_window)
        f = a_open.font()
        f.setBold(True)
        a_open.setFont(f)
        self.menu.addAction(a_open)
        self.menu.addSeparator()
        self.a_status = self._info(f"No {device.display_name()} connected")
        self.a_detail = self._info("")
        self.a_warn = self._info("")
        self.menu.addSeparator()

        self.a_rates = self._check("Use the music's sample rate", self.toggle_rates)
        self.a_awake = self._check("Keep the K7 awake (no cut-off sounds)", self.toggle_keepawake)

        self.m_eq = self.menu.addMenu("Headphone EQ")
        self.m_eq.aboutToShow.connect(self.build_eq_menu)

        self.m_lat = self.menu.addMenu("Latency")
        self.g_lat = QtGui.QActionGroup(self.m_lat)
        self.lat_actions = {}
        for name, (v, desc) in latency.PRESETS.items():
            a = QtGui.QAction(f"{name} ({v[0]} samples)", self.m_lat)
            a.setToolTip(desc)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, n=name: self.run(lambda: latency.apply(n)))
            self.g_lat.addAction(a)
            self.m_lat.addAction(a)
            self.lat_actions[name] = a

        self.m_notify = self.menu.addMenu("Notifications")
        self.a_n_rate = QtGui.QAction("When the sample rate changes", self.m_notify)
        self.a_n_res = QtGui.QAction("When something starts being resampled", self.m_notify)
        for a, key in ((self.a_n_rate, "notify_rate"), (self.a_n_res, "notify_resample")):
            a.setCheckable(True)
            a.toggled.connect(lambda on, k=key: settings.set(**{k: on}))
            self.m_notify.addAction(a)

        self.menu.addSeparator()
        self.a_auto = self._check("Start at login", self.toggle_autostart)
        self.a_auto.setChecked(autostart_path().is_file())
        a_quit = QtGui.QAction("Quit", self.menu)
        a_quit.triggered.connect(app.quit)
        self.menu.addAction(a_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)
        self.tray.show()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()

    def _info(self, text: str) -> QtGui.QAction:
        a = QtGui.QAction(text, self.menu)
        a.setEnabled(False)
        self.menu.addAction(a)
        return a

    def _check(self, text: str, slot) -> QtGui.QAction:
        a = QtGui.QAction(text, self.menu)
        a.setCheckable(True)
        a.toggled.connect(slot)
        self.menu.addAction(a)
        return a

    @staticmethod
    def _set_checked(a, on: bool):
        a.blockSignals(True)
        a.setChecked(on)
        a.blockSignals(False)

    # ------------------------------------------------------------ refresh
    def refresh(self):
        # while the window is open it polls, and its snapshots reach us too
        if self.polling or (self.window and self.window.isVisible()):
            return
        self.polling = True

        def done(s):
            self.polling = False
            if not isinstance(s, Exception):
                self.update(s)
        self.worker.run(snapshot.take, done)

    def run(self, fn, message: str | None = None):
        """Run a settings change on the worker, report errors, then refresh."""
        def done(r):
            if isinstance(r, Exception):
                self.tray.showMessage(self.title(), str(r), state_icon("disconnected"), 6000)
            elif message:
                self.tray.showMessage(self.title(), message.format(r) if "{}" in message else message,
                                      state_icon("eq" if "EQ" in message else "idle"), 3000)
            self.polling = False
            self.refresh()
            if self.window and self.window.isVisible():
                self.window.refresh()
        self.worker.run(fn, done)

    def title(self) -> str:
        return self.snap.k7.name if self.snap and self.snap.k7 else device.display_name()

    def notify_changes(self, s: snapshot.Snapshot, prefs: dict):
        """Rate-change and resampling notifications (off by default)."""
        rate = s.k7.stream.describe() if s.playing else None
        if prefs["notify_rate"] and rate and self.last_rate and rate != self.last_rate:
            self.tray.showMessage(self.title(), f"Now playing {rate}", state_icon(s.css_class()), 3000)
        if rate:
            self.last_rate = rate
        resampled = {x.app for x in s.resampled}
        new = resampled - self.last_resampled
        if prefs["notify_resample"] and new and s.sink and s.sink.rate:
            why = s.why_resampled()
            self.tray.showMessage(self.title(), why[0] if why else
                                  f"{', '.join(sorted(new))} resampled to {format_rate(s.sink.rate)}",
                                  state_icon("resampled"), 6000)
        self.last_resampled = resampled

    def update(self, s: snapshot.Snapshot):
        self.snap = s
        connected = s.connected
        if self.was_connected is not None and connected != self.was_connected:
            self.tray.showMessage(self.title(), "Connected" if connected else "Disconnected",
                                  state_icon("idle" if connected else "disconnected"), 3000)
        self.was_connected = connected
        prefs = settings.load()
        self.notify_changes(s, prefs)
        self._set_checked(self.a_n_rate, bool(prefs["notify_rate"]))
        self._set_checked(self.a_n_res, bool(prefs["notify_resample"]))

        self.tray.setIcon(state_icon(s.css_class()))
        if not connected:
            self.a_status.setText(f"No {self.title()} connected")
            self.a_detail.setVisible(False)
            self.a_warn.setVisible(False)
            self.tray.setToolTip(f"{self.title()}: not connected")
        else:
            desc = s.k7.stream.describe()
            self.a_status.setText(f"{self.title()}: {desc}")
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
            tip = [f"{self.title()}: {desc}"] + ([f"EQ: {s.eq_profile}"] if s.eq_on else []) + why
            self.tray.setToolTip("\n".join(tip))

        self._set_checked(self.a_rates, (s.allowed_rates is not None and len(s.allowed_rates) > 1)
                          or rates.is_enabled())
        self._set_checked(self.a_awake, bool(s.keepawake) or s.keepawake_config)
        for name, a in self.lat_actions.items():
            a.setChecked(name == s.latency)

    # ------------------------------------------------------------ menus
    def build_eq_menu(self):
        m = self.m_eq
        m.clear()
        on = bool(self.snap and self.snap.eq_on)
        sel = eq.selected_profile()
        g = QtGui.QActionGroup(m)
        off = QtGui.QAction("Off (bit-perfect)", m)
        off.setCheckable(True)
        off.setChecked(not on)
        off.triggered.connect(lambda: self.run(eq.disable, "EQ off"))
        g.addAction(off)
        m.addAction(off)
        for name in eq.list_profiles():
            a = QtGui.QAction(name, m)
            a.setCheckable(True)
            a.setChecked(on and name == sel)
            a.triggered.connect(lambda _=False, n=name: self.run(lambda: eq.enable(n), "EQ on: {}"))
            g.addAction(a)
            m.addAction(a)
        m.addSeparator()
        find = QtGui.QAction("Find my headphones…", m)
        find.triggered.connect(self.open_window)
        m.addAction(find)

    # ------------------------------------------------------------ actions
    def open_window(self):
        if self.window is None:
            from .window import Window
            self.window = Window(self.worker, standalone=False)
            self.window.state_changed.connect(self.update)
        self.window.present()

    def on_activated(self, reason):
        if reason == W.QSystemTrayIcon.ActivationReason.Trigger:
            if self.window and self.window.isVisible():
                self.window.hide()
            else:
                self.open_window()

    def toggle_rates(self, on: bool):
        self.run(rates.enable if on else rates.disable)

    def toggle_keepawake(self, on: bool):
        self.run(keepawake.enable if on else keepawake.disable)

    def toggle_autostart(self, on: bool):
        path = autostart_path()
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(autostart_entry())
        elif path.is_file():
            path.unlink()

    def shutdown(self):
        self.timer.stop()
        if self.window:
            self.window.shutdown()
        self.worker.shutdown()


def main() -> int:
    app = make_app()
    app.setQuitOnLastWindowClosed(False)
    if not W.QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available; opening the window instead.", file=sys.stderr)
        from .window import main as window_main
        return window_main()
    tray = Tray(app)

    def cleanup():
        tray.shutdown()
        app.quit()
    return run_app(app, cleanup)

