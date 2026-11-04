"""The k7ctl window: status, bit-perfect settings, headphone EQ and device info.

Everything slow (PipeWire queries, WirePlumber restarts, AutoEQ downloads)
runs on the Worker thread (see qt.py), so the window never freezes.
"""

from __future__ import annotations

import sys

from . import (device, eq, exclusive, firmware, keepawake, latency, pipewire, rates, resample,
               settings, snapshot)
from .curvewidget import EqCurve
from .device import format_rate
from .i18n import tr
from .qt import QtCore, QtGui, QtWidgets, Signal, Worker, make_app, run_app, state_icon, theme_icon

W = QtWidgets
Qt = QtCore.Qt

REFRESH_MS = 2000

# Breeze colours
GOOD, WARN, BAD = (39, 174, 96), (246, 116, 0), (218, 68, 83)


LATENCY_NAMES = {"default": tr("default"), "stable": tr("stable"), "low-latency": tr("low latency")}
LATENCY_DESC = {
    "default": tr("PipeWire's defaults"),
    "stable": tr("bigger buffers: fixes crackles at high sample rates, more latency"),
    "low-latency": tr("small buffers for games and calls; may crackle at high rates"),
}


CROSSFEED_NAMES = {"off": tr("Off"), "light": tr("Light"), "medium": tr("Medium"), "strong": tr("Strong")}
RESAMPLE_NAMES = {"default": tr("Default (PipeWire's)"), "high": tr("High"), "max": tr("Maximum")}
RESAMPLE_DESC = {
    "default": tr("Very clean, but treble rolls off from about 19 kHz when converting."),
    "high": tr("Flat to 20.5 kHz when converting; a little more CPU."),
    "max": tr("Flat to 21.5 kHz when converting; most CPU."),
}


def describe(st) -> str:
    """device.Stream.describe(), translated."""
    if st.state == "closed" or not st.rate:
        return tr("idle")
    text = st.describe()
    if st.is_dsd:
        return text.replace("native", tr("native"))
    return text.replace("(paused)", tr("(paused)"))


def hint(text: str = "") -> W.QLabel:
    label = W.QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # greyed out, like help text
    return label


def colored(label: W.QLabel, rgb: tuple[int, int, int] | None):
    label.setStyleSheet("" if rgb is None else "color: rgb(%d,%d,%d); font-weight: bold;" % rgb)


class Window(W.QMainWindow):
    state_changed = Signal(object)   # every fresh snapshot (the tray listens)

    def __init__(self, worker: Worker | None = None, standalone: bool = True):
        super().__init__()
        self.worker = worker or Worker()
        self.own_worker = worker is None
        self.standalone = standalone
        self.snap: snapshot.Snapshot | None = None
        self.closed = False
        self.refreshing = False
        self.acting = 0
        self.search_hits: list[eq.AutoEqEntry] = []

        self.setWindowTitle("FiiO K7")
        self.setWindowIcon(theme_icon())
        self.build()
        self.update_enabled()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.auto_refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    # ------------------------------------------------------------------ layout
    def build(self):
        central = W.QWidget()
        root = W.QVBoxLayout(central)
        root.setSpacing(12)
        self.setCentralWidget(central)

        # header
        head = W.QHBoxLayout()
        self.icon = W.QLabel()
        self.icon.setPixmap(theme_icon().pixmap(48, 48))
        head.addWidget(self.icon)
        titles = W.QVBoxLayout()
        self.title = W.QLabel("FiiO K7")
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.title.setFont(font)
        self.subtitle = W.QLabel(tr("Looking for the K7…"))
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.refresh_btn = W.QToolButton()
        self.refresh_btn.setIcon(theme_icon("view-refresh"))
        self.refresh_btn.setToolTip(tr("Refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # message banner
        self.banner = W.QFrame()
        self.banner.setObjectName("banner")
        row = W.QHBoxLayout(self.banner)
        row.setContentsMargins(10, 6, 4, 6)
        self.banner_text = W.QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.banner_text, 1)
        close = W.QToolButton()
        close.setIcon(theme_icon("window-close", "dialog-close"))
        close.setAutoRaise(True)
        close.setToolTip(tr("Dismiss"))
        close.clicked.connect(self.banner.hide)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.banner.hide()
        root.addWidget(self.banner)
        self.banner_timer = QtCore.QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # scrolling sections
        content = W.QWidget()
        sections = W.QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 0, 0)
        sections.setSpacing(12)
        self.scroll = W.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        sections.addWidget(self.build_now_playing())
        sections.addWidget(self.build_playback())
        sections.addWidget(self.build_eq())
        sections.addWidget(self.build_device())
        sections.addStretch(1)
        self.fit_to_screen()

    def build_now_playing(self) -> W.QGroupBox:
        box = self.now_box = W.QGroupBox(tr("Now playing"))
        form = W.QFormLayout(box)
        self.playing = W.QLabel("–")
        self.fmt = W.QLabel("–")
        self.bitperfect = W.QLabel("–")
        form.addRow(tr("Playing:"), self.playing)
        form.addRow(tr("Format:"), self.fmt)
        form.addRow(tr("Bit-perfect:"), self.bitperfect)

        self.streams = W.QTableWidget(0, 3)
        self.streams.setHorizontalHeaderLabels([tr("Application"), tr("Sample rate"), ""])
        self.streams.verticalHeader().setVisible(False)
        self.streams.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.streams.setSelectionMode(W.QAbstractItemView.SelectionMode.NoSelection)
        self.streams.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h = self.streams.horizontalHeader()
        h.setSectionResizeMode(0, W.QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, W.QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, W.QHeaderView.ResizeMode.ResizeToContents)
        self.streams.verticalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Fixed)
        form.addRow(self.streams)
        self.why = hint()
        self.why.setEnabled(True)
        colored(self.why, WARN)
        self.why.hide()
        form.addRow(self.why)
        return box

    def build_playback(self) -> W.QGroupBox:
        box = self.playback_box = W.QGroupBox(tr("Bit-perfect playback"))
        form = W.QFormLayout(box)
        form.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.rates_cb = W.QCheckBox(tr("Use the music's own sample rate"))
        self.rates_cb.toggled.connect(self.set_rates)
        form.addRow(tr("Sample rate:"), self.rates_cb)
        form.addRow(hint(tr("Otherwise PipeWire resamples everything to 48 kHz. It can only switch "
                         "when nothing else is playing at another rate.")))

        vol = W.QHBoxLayout()
        self.volume = W.QLabel("–")
        self.volume_btn = W.QPushButton(tr("Set to 100%"))
        self.volume_btn.clicked.connect(self.volume_max)
        vol.addWidget(self.volume)
        vol.addStretch(1)
        vol.addWidget(self.volume_btn)
        form.addRow(tr("PipeWire volume:"), vol)
        form.addRow(hint(tr("The K7 has no volume control over USB, so this is software volume: "
                         "below 100% it reduces resolution. Use the K7's knob instead.")))

        out = W.QHBoxLayout()
        self.default_out = W.QLabel("–")
        self.default_btn = W.QPushButton(tr("Make the K7 the default"))
        self.default_btn.clicked.connect(self.make_default)
        out.addWidget(self.default_out)
        out.addStretch(1)
        out.addWidget(self.default_btn)
        form.addRow(tr("Default output:"), out)

        self.awake_cb = W.QCheckBox(tr("Keep the K7 awake when idle"))
        self.awake_cb.toggled.connect(self.set_keepawake)
        form.addRow(tr("Keep awake:"), self.awake_cb)
        form.addRow(hint(tr("Stops the start of sounds being cut off after a pause. "
                         "Changing it restarts WirePlumber (about a second of silence).")))

        self.excl_cb = W.QCheckBox(tr("Release the K7 for players that use it directly"))
        self.excl_cb.toggled.connect(self.set_exclusive)
        form.addRow(tr("Direct players:"), self.excl_cb)
        form.addRow(hint(tr("For native DSD: PipeWire can't send DSD, so players like MPD or Strawberry need "
                         "the K7 to themselves (ALSA device hw:CARD=K7). While released, other apps play "
                         "through another output. It stays released until you untick this.")))

        self.latency_combo = W.QComboBox()
        for name, (v, desc) in latency.PRESETS.items():
            self.latency_combo.addItem(tr("{preset} ({samples} samples)").format(
                preset=LATENCY_NAMES.get(name, name), samples=v[0]), name)
            self.latency_combo.setItemData(self.latency_combo.count() - 1, LATENCY_DESC.get(name, desc),
                                           Qt.ItemDataRole.ToolTipRole)
        self.latency_combo.activated.connect(self.set_latency)
        form.addRow(tr("Latency:"), self.latency_combo)
        self.latency_hint = hint()
        form.addRow(self.latency_hint)

        self.resample_combo = W.QComboBox()
        for name in resample.PRESETS:
            self.resample_combo.addItem(RESAMPLE_NAMES[name], name)
        self.resample_combo.activated.connect(self.set_resample)
        form.addRow(tr("Resampler:"), self.resample_combo)
        self.resample_hint = hint()
        form.addRow(self.resample_hint)

        self.notify_rate_cb = W.QCheckBox(tr("When the sample rate changes"))
        self.notify_rate_cb.toggled.connect(lambda on: self.save_setting(notify_rate=on))
        self.notify_res_cb = W.QCheckBox(tr("When something starts being resampled"))
        self.notify_res_cb.toggled.connect(lambda on: self.save_setting(notify_resample=on))
        nbox = W.QVBoxLayout()
        nbox.addWidget(self.notify_rate_cb)
        nbox.addWidget(self.notify_res_cb)
        form.addRow(tr("Notify:"), nbox)
        form.addRow(hint(tr("Notifications come from the tray icon, so it needs to be running.")))
        return box

    def build_eq(self) -> W.QGroupBox:
        box = self.eq_box = W.QGroupBox(tr("Headphone EQ and crossfeed"))
        lay = W.QVBoxLayout(box)

        top = W.QHBoxLayout()
        self.eq_cb = W.QCheckBox(tr("EQ on"))
        self.eq_cb.toggled.connect(self.set_eq)
        top.addWidget(self.eq_cb)
        self.profile_combo = W.QComboBox()
        self.profile_combo.setSizeAdjustPolicy(
            W.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setMinimumContentsLength(20)
        self.profile_combo.activated.connect(self.pick_profile)
        top.addWidget(self.profile_combo, 1)
        self.import_btn = W.QPushButton(theme_icon("document-open"), tr("Import…"))
        self.import_btn.setToolTip(tr("Import an Equalizer APO / ParametricEQ.txt file"))
        self.import_btn.clicked.connect(self.import_profile)
        top.addWidget(self.import_btn)
        self.remove_btn = W.QPushButton(theme_icon("edit-delete"), tr("Remove"))
        self.remove_btn.clicked.connect(self.remove_profile)
        top.addWidget(self.remove_btn)
        lay.addLayout(top)
        self.curve = EqCurve()
        lay.addWidget(self.curve)
        self.curve_hint = hint(tr("Solid: what you hear (with the preamp that prevents clipping). "
                               "Dashed: the correction's shape."))
        lay.addWidget(self.curve_hint)
        cf = W.QHBoxLayout()
        cf.addWidget(W.QLabel(tr("Crossfeed:")))
        self.crossfeed_combo = W.QComboBox()
        for name in ("off", *eq.CROSSFEED):
            self.crossfeed_combo.addItem(CROSSFEED_NAMES[name], name)
        self.crossfeed_combo.activated.connect(self.set_crossfeed)
        cf.addWidget(self.crossfeed_combo)
        cf.addStretch(1)
        lay.addLayout(cf)
        lay.addWidget(hint(tr("Crossfeed mixes a little of each channel's bass into the other, as with speakers, "
                              "so hard-panned recordings sound less 'inside your head'. Centred sound is unchanged. "
                              "Light, medium and strong are the classic bs2b settings.")))
        lay.addWidget(hint(tr("With EQ or crossfeed on, playback is processed and no longer bit-perfect. "
                              "Turn them off for bit-perfect listening.")))

        search = W.QHBoxLayout()
        self.search_edit = W.QLineEdit()
        self.search_edit.setPlaceholderText(tr("Find your headphones, e.g. HD 600, Sundara, Moondrop Aria"))
        self.search_edit.returnPressed.connect(self.search)
        self.search_edit.setClearButtonEnabled(True)
        search.addWidget(self.search_edit, 1)
        self.search_btn = W.QPushButton(theme_icon("edit-find", "search"), tr("Search AutoEQ"))
        self.search_btn.clicked.connect(self.search)
        search.addWidget(self.search_btn)
        lay.addLayout(search)

        self.results = W.QListWidget()
        self.results.setMaximumHeight(150)
        self.results.itemDoubleClicked.connect(lambda _i: self.download())
        self.results.currentRowChanged.connect(lambda _r: self.update_enabled())
        self.results.hide()
        lay.addWidget(self.results)
        self.download_btn = W.QPushButton(theme_icon("download", "go-down"), tr("Download and turn EQ on"))
        self.download_btn.clicked.connect(self.download)
        self.download_btn.hide()
        dl = W.QHBoxLayout()
        dl.addStretch(1)
        dl.addWidget(self.download_btn)
        lay.addLayout(dl)
        lay.addWidget(hint(tr("Profiles from AutoEQ (github.com/jaakkopasanen/AutoEq). Several sources may have "
                           "measured the same headphones; oratory1990 and crinacle are good first choices.")))
        self.fill_profiles()
        return box

    def build_device(self) -> W.QGroupBox:
        box = self.device_box = W.QGroupBox(tr("Device"))
        form = W.QFormLayout(box)
        self.device_combo = W.QComboBox()
        self.device_combo.activated.connect(self.pick_device)
        form.addRow(tr("DAC:"), self.device_combo)
        self.dev = {}
        for key, label in (("firmware", tr("Firmware:")), ("usb", tr("USB:")), ("alsa", tr("ALSA device:")),
                           ("rates", tr("Sample rates:")), ("formats", tr("Formats:"))):
            w = W.QLabel("–")
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.dev[key] = w
            form.addRow(label, w)
        row = W.QHBoxLayout()
        self.diag_btn = W.QPushButton(theme_icon("edit-copy"), tr("Copy diagnostics"))
        self.diag_btn.setToolTip(tr("Copy a report for bug reports to the clipboard"))
        self.diag_btn.clicked.connect(self.copy_diagnostics)
        row.addWidget(self.diag_btn)
        row.addStretch(1)
        self.fw_check_btn = W.QPushButton(theme_icon("internet-web-browser", "globe"), tr("Check for updates"))
        self.fw_check_btn.setToolTip(tr("Open FiiO's firmware downloads to compare with the installed version"))
        self.fw_check_btn.clicked.connect(firmware.open_download_page)
        row.addWidget(self.fw_check_btn)
        fw = W.QPushButton(tr("How to update"))
        fw.clicked.connect(lambda: W.QMessageBox.information(
            self, tr("Firmware"), firmware.update_help(self.snap.k7 if self.snap else None)))
        row.addWidget(fw)
        form.addRow(row)
        self.physical_hint = hint(tr("The K7's volume knob, gain switch and input/output selector are "
                                  "physical only: the K7 has no way to change them over USB."))
        form.addRow(self.physical_hint)
        self.fill_devices()
        return box

    def fit_to_screen(self):
        content = self.scroll.widget()
        want_h = (self.sizeHint().height() - self.scroll.sizeHint().height()
                  + content.sizeHint().height() + 8)
        want_w = max(620, content.sizeHint().width()
                     + self.scroll.verticalScrollBar().sizeHint().width() + 24)
        screen = self.screen() or W.QApplication.primaryScreen()
        if screen is not None:
            want_h = min(want_h, int(screen.availableGeometry().height() * 0.9))
        self.resize(want_w, want_h)

    # ------------------------------------------------------------------ state
    def auto_refresh(self):
        if self.isVisible() and not self.acting:
            self.refresh()

    def refresh(self):
        if self.closed or self.refreshing:
            return
        self.refreshing = True
        self.worker.run(snapshot.take, self.on_snapshot)

    def on_snapshot(self, s):
        self.refreshing = False
        if self.closed:
            return
        if isinstance(s, Exception):
            self.say(tr("Couldn't read the status: {error}").format(error=s), 8000, "error")
            return
        self.snap = s
        self.state_changed.emit(s)
        self.fill(s)
        self.update_enabled()

    @staticmethod
    def _set(widget, on: bool):
        widget.blockSignals(True)
        widget.setChecked(on)
        widget.blockSignals(False)

    def fill(self, s: snapshot.Snapshot):
        self.icon.setPixmap(state_icon(s.css_class()).pixmap(48, 48))
        k7 = s.k7
        name = k7.name if k7 else device.display_name()
        self.title.setText(name)
        self.setWindowTitle(name)
        is_k7 = (k7.is_k7 if k7 else device.target_id() == device.DEFAULT_ID)
        self.fw_check_btn.setVisible(is_k7)
        self.physical_hint.setVisible(is_k7)
        self.fill_devices()
        settings_now = settings.load()
        self._set(self.notify_rate_cb, bool(settings_now["notify_rate"]))
        self._set(self.notify_res_cb, bool(settings_now["notify_resample"]))
        if not k7:
            self.subtitle.setText(tr("Not connected. Plug in the K7 and switch it to USB input.") if is_k7
                                  else tr("Not connected ({device}).").format(device=device.target_id()))
            for w in (self.playing, self.fmt, self.bitperfect, self.volume, self.default_out):
                w.setText("–")
            colored(self.bitperfect, None)
            self.streams.setRowCount(0)
            self.why.hide()
            for w in self.dev.values():
                w.setText("–")
        else:
            st = k7.stream
            desc = describe(st)
            bp = s.bit_perfect
            parts = [tr("Idle") if st.state == "closed" or not st.rate
                     else tr("Playing {what}").format(what=desc)]
            if s.direct:
                parts.append(tr("directly by {program}").format(program=s.direct.split(" (")[0]))
            elif s.exclusive:
                parts.append(tr("released for direct players"))
            if s.eq_on:
                parts.append(tr("EQ: {profile}").format(profile=s.eq_profile))
            if s.crossfeed:
                parts.append(tr("crossfeed: {preset}").format(preset=CROSSFEED_NAMES[s.crossfeed]))
            if not s.processing and bp is not None:      # EQ/crossfeed already say why
                parts.append(tr("bit-perfect") if bp else tr("not bit-perfect"))
            self.subtitle.setText(", ".join(parts))
            self.playing.setText(desc)
            self.fmt.setText(tr("{format}, {channels} channels").format(format=st.format, channels=st.channels)
                             if st.format and st.state != "closed" else "–")
            if bp is None:
                self.bitperfect.setText(tr("– (nothing playing)"))
                colored(self.bitperfect, None)
            else:
                reasons = []
                if s.eq_on:
                    reasons.append(tr("EQ is on"))
                if s.crossfeed:
                    reasons.append(tr("crossfeed is on"))
                if s.resampled:
                    reasons.append(tr("resampled"))
                if s.sink and s.sink.volume is not None and (s.sink.volume < 0.999 or s.sink.muted):
                    reasons.append(tr("volume below 100%"))
                self.bitperfect.setText(tr("Yes") if bp else
                                        tr("No: {reasons}").format(reasons=", ".join(reasons)))
                colored(self.bitperfect, GOOD if bp else WARN)
            self.fill_streams(s)

            sink = s.sink
            if sink and sink.volume is not None:
                self.volume.setText(f"{sink.volume * 100:.0f}%" + (tr(" (muted)") if sink.muted else ""))
                colored(self.volume, None if sink.volume >= 0.999 and not sink.muted else WARN)
            else:
                self.volume.setText("–")
            if sink:
                self.default_out.setText(f"{k7.name} + EQ" if sink.eq_is_default else
                                         k7.name if sink.is_default else tr("another output"))

            self.dev["firmware"].setText(k7.firmware or tr("unknown"))
            self.dev["usb"].setText(k7.usb_speed or tr("unknown"))
            self.dev["alsa"].setText(k7.alsa_device)
            self.dev["rates"].setText(", ".join(format_rate(r) for r in k7.supported_rates))
            self.dev["formats"].setText(", ".join(
                tr("native DSD") if f.startswith("DSD") else f for f in k7.supported_formats))

        self._set(self.rates_cb, (s.allowed_rates is not None and len(s.allowed_rates) > 1)
                  or rates.is_enabled())
        self._set(self.awake_cb, bool(s.keepawake) or s.keepawake_config)
        self._set(self.excl_cb, s.exclusive)
        idx = self.latency_combo.findData(s.latency)
        if idx >= 0:
            self.latency_combo.setCurrentIndex(idx)
            desc = LATENCY_DESC.get(s.latency, latency.PRESETS[s.latency][1])
            self.latency_hint.setText(desc[:1].upper() + desc[1:] + ".")
        elif s.latency == "custom":
            self.latency_hint.setText(tr("Currently a custom setting (not from k7ctl)."))
        self._set(self.eq_cb, s.eq_on)
        self.crossfeed_combo.setCurrentIndex(max(0, self.crossfeed_combo.findData(eq.crossfeed_preset() or "off")))
        rs = s.resample if s.resample in resample.PRESETS else None
        if rs:
            self.resample_combo.setCurrentIndex(self.resample_combo.findData(rs))
            self.resample_hint.setText(RESAMPLE_DESC[rs] + " " + tr("Used only when PipeWire has to convert "
                                                                   "sample rates."))
        self.fill_profiles()

    def fill_streams(self, s: snapshot.Snapshot):
        sink = s.sink
        rows = sink.playing if sink else []
        self.streams.setRowCount(len(rows))
        for i, x in enumerate(rows):
            self.streams.setItem(i, 0, W.QTableWidgetItem(x.app))
            self.streams.setItem(i, 1, W.QTableWidgetItem(x.rate_text or "–"))
            if sink.rate and x.rate and not x.dsd and x.rate != sink.rate:
                st = W.QTableWidgetItem(tr("resampled to {rate}").format(rate=format_rate(sink.rate)))
                st.setForeground(QtGui.QBrush(QtGui.QColor(*WARN)))
            elif x.is_eq:
                st = W.QTableWidgetItem("EQ")
            else:
                st = W.QTableWidgetItem(tr("unchanged"))
                st.setForeground(QtGui.QBrush(QtGui.QColor(*GOOD)))
            self.streams.setItem(i, 2, st)
        self.streams.setVisible(bool(rows))
        if rows:
            hh = self.streams.horizontalHeader().sizeHint().height()
            rh = self.streams.verticalHeader().defaultSectionSize()
            self.streams.setFixedHeight(hh + min(len(rows), 5) * rh + 2 * self.streams.frameWidth())
        why = s.why_resampled(tr)
        self.why.setText("\n".join(why))
        self.why.setVisible(bool(why))

    def show_curve(self):
        name = self.profile_combo.currentData()
        profile = None
        if name:
            try:
                profile = eq.parse_profile(eq.profile_path(name).read_text(errors="replace"))
            except (OSError, eq.EqError):
                profile = None
        self.curve.set_profile(profile)
        self.curve_hint.setVisible(profile is not None and bool(profile.safe_preamp))

    def fill_devices(self):
        cards = [c for c in device.list_usb_cards() if c.playback]
        conf = device.configured_id()
        items = [("auto", tr("Auto (FiiO K7)"))] + [(c.usb_id, f"{c.name}  ({c.usb_id})") for c in cards]
        if conf != "auto" and conf not in {c.usb_id for c in cards}:
            items.append((conf, tr("{device} (not connected)").format(device=conf)))
        current = [(self.device_combo.itemData(i), self.device_combo.itemText(i))
                   for i in range(self.device_combo.count())]
        if current != items or self.device_combo.currentData() != conf:
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for data, label in items:
                self.device_combo.addItem(label, data)
            self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(conf)))
            self.device_combo.blockSignals(False)

    def fill_profiles(self):
        profiles = eq.list_profiles()
        sel = eq.selected_profile()
        current = [self.profile_combo.itemData(i) for i in range(self.profile_combo.count())]
        if (current != profiles or self.profile_combo.count() == 0
                or (profiles and self.profile_combo.currentData() != sel)):
            self.profile_combo.blockSignals(True)
            self.profile_combo.clear()
            if not profiles:
                self.profile_combo.addItem(tr("No profiles yet: search below"), None)
            for p in profiles:
                self.profile_combo.addItem(p, p)
            if sel in profiles:
                self.profile_combo.setCurrentIndex(profiles.index(sel))
            self.profile_combo.blockSignals(False)
            self.show_curve()

    def update_enabled(self):
        if self.closed:
            return
        busy = self.acting > 0
        connected = bool(self.snap and self.snap.connected)
        has_profiles = self.profile_combo.currentData() is not None
        for w in (self.rates_cb, self.latency_combo):
            w.setEnabled(not busy)
        for w in (self.awake_cb, self.default_btn, self.excl_cb):
            w.setEnabled(not busy and connected)
        self.default_btn.setVisible(bool(self.snap and self.snap.sink and not self.snap.sink.is_default
                                         and not self.snap.sink.eq_is_default))
        sink = self.snap.sink if self.snap else None
        self.volume_btn.setEnabled(not busy and bool(sink and sink.volume is not None
                                                     and (sink.volume < 0.999 or sink.muted)))
        self.eq_cb.setEnabled(not busy and connected and (has_profiles or self.eq_cb.isChecked()))
        self.crossfeed_combo.setEnabled(not busy and connected)
        self.resample_combo.setEnabled(not busy)
        self.profile_combo.setEnabled(not busy and has_profiles)
        self.remove_btn.setEnabled(not busy and has_profiles)
        self.import_btn.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy and self.results.currentRow() >= 0)
        self.refresh_btn.setEnabled(not busy)

    # ---------------------------------------------------------------- messages
    def say(self, text: str, ms: int = 6000, kind: str = "info"):
        rgb = {"ok": GOOD, "warn": WARN, "error": BAD}.get(kind)
        if rgb is None:
            c = self.palette().color(QtGui.QPalette.ColorRole.Highlight)
            rgb = (c.red(), c.green(), c.blue())
        self.banner.setStyleSheet(
            "#banner { background: rgba(%d,%d,%d,40); border: 1px solid rgb(%d,%d,%d);"
            " border-radius: 4px; }" % (rgb + rgb))
        self.banner_text.setText(text)
        self.banner.show()
        if ms:
            self.banner_timer.start(ms)
        else:
            self.banner_timer.stop()

    def message(self) -> str:
        return "" if self.banner.isHidden() else self.banner_text.text()

    def act(self, doing: str, fn, done_text: str | None = None, on_done=None):
        """Run fn() on the worker, show progress and the result, then refresh."""
        self.say(doing + "…", 0)
        self.acting += 1
        self.update_enabled()

        def finished(r):
            self.acting -= 1
            if self.closed:
                return
            if isinstance(r, Exception):
                self.say(tr("{action} failed: {error}").format(action=doing, error=r), 12000, "error")
            else:
                if on_done:
                    on_done(r)
                if done_text:
                    self.say(done_text if "{}" not in done_text else done_text.format(r), 5000, "ok")
                elif not on_done:
                    self.banner.hide()
            self.refresh()
        self.worker.run(fn, finished)

    # ---------------------------------------------------------------- actions
    def set_rates(self, on: bool):
        if on:
            self.act(tr("Enabling native sample rates"), rates.enable,
                     tr("PipeWire now plays at the music's own sample rate."))
        else:
            self.act(tr("Going back to 48 kHz only"), rates.disable, tr("PipeWire resamples to 48 kHz again."))

    def set_keepawake(self, on: bool):
        if on:
            self.act(tr("Restarting WirePlumber to keep the K7 awake"), keepawake.enable,
                     tr("The K7 now stays awake when idle."))
        else:
            self.act(tr("Restarting WirePlumber"), keepawake.disable,
                     tr("The K7 suspends after a few idle seconds again."))

    def set_exclusive(self, on: bool):
        if on:
            self.act(tr("Releasing the K7 from PipeWire"), exclusive.enable, "{}")
        else:
            self.act(tr("Giving the K7 back to PipeWire"), exclusive.disable, "{}")

    def set_crossfeed(self, _index: int):
        name = self.crossfeed_combo.currentData()
        if name == (eq.crossfeed_preset() or "off"):
            return
        if name == "off":
            self.act(tr("Turning crossfeed off"), lambda: eq.set_crossfeed(None), tr("Crossfeed off."))
        else:
            self.act(tr("Turning crossfeed on ({preset})").format(preset=CROSSFEED_NAMES[name]),
                     lambda: eq.set_crossfeed(name),
                     tr("Crossfeed on: {preset}. Playback is processed (not bit-perfect).").format(
                         preset=CROSSFEED_NAMES[name]))

    def set_resample(self, _index: int):
        name = self.resample_combo.currentData()
        if name == resample.current():
            return
        now = W.QMessageBox.question(
            self, tr("Resampler quality"),
            tr("Apply it to running apps now? This restarts PipeWire's PulseAudio service, so sound from "
               "Firefox and other apps stops for a moment. Otherwise it applies to apps started from now on."))
        restart = now == W.QMessageBox.StandardButton.Yes

        def job():
            resample.apply(name, restart=restart)
            eq.restart_if_running()
        self.act(tr("Setting the resampler quality"), job,
                 tr("Resampler quality: {preset}.").format(preset=RESAMPLE_NAMES[name]))

    def set_latency(self, _index: int):
        name = self.latency_combo.currentData()
        label = LATENCY_NAMES.get(name, name)
        self.act(tr("Setting latency to {preset}").format(preset=label), lambda: latency.apply(name),
                 tr("Latency: {preset}.").format(preset=label))

    def volume_max(self):
        sink = self.snap.sink if self.snap else None
        if not sink:
            return
        ok = W.QMessageBox.question(
            self, tr("Set volume to 100%"),
            tr("This sets PipeWire's volume for the K7 to 100% so the DAC gets full resolution; "
            "you then control the volume with the K7's knob.\n\nTurn the K7's knob down first. Continue?"))
        if ok == W.QMessageBox.StandardButton.Yes:
            self.act(tr("Setting the volume"), lambda: pipewire.set_full_volume(sink),
                     tr("Volume 100%. Use the K7's knob for volume."))

    def make_default(self):
        sink = self.snap.sink if self.snap else None
        if sink:
            self.act(tr("Making the K7 the default output"), lambda: pipewire.make_default(sink),
                     tr("The K7 is the default output."))

    def set_eq(self, on: bool):
        if on:
            name = self.profile_combo.currentData()
            if not name:
                self._set(self.eq_cb, False)
                self.say(tr("Find your headphones below first."), 6000, "warn")
                return
            self.act(tr("Turning EQ on ({profile})").format(profile=name), lambda: eq.enable(name),
                     tr("EQ on: {}. Playback is processed (not bit-perfect)."))
        else:
            self.act(tr("Turning EQ off"), eq.disable, tr("EQ off. The K7 is the default output again."))

    def pick_profile(self, _index: int):
        name = self.profile_combo.currentData()
        if not name:
            return
        if self.snap and self.snap.eq_on:
            self.act(tr("Switching EQ to {profile}").format(profile=name), lambda: eq.enable(name), tr("EQ: {}."))
        else:
            eq.save_state(profile=name)
            self.say(tr("Selected {profile}. Tick “EQ on” to use it.").format(profile=name), 4000)
        self.show_curve()

    def import_profile(self):
        path, _ = W.QFileDialog.getOpenFileName(
            self, tr("Import a parametric EQ"), "", tr("EQ files (*.txt);;All files (*)"))
        if not path:
            return
        self.act(tr("Importing"), lambda: eq.import_file(path)[0], tr("Imported {}."),
                 on_done=lambda name: eq.save_state(profile=name))

    def remove_profile(self):
        name = self.profile_combo.currentData()
        if not name:
            return
        if W.QMessageBox.question(self, tr("Remove profile"),
                                  tr("Remove the EQ profile “{profile}”?").format(profile=name)) \
                != W.QMessageBox.StandardButton.Yes:
            return
        if self.snap and self.snap.eq_on and eq.selected_profile() == name:
            self.act(tr("Turning EQ off"), lambda: (eq.disable(), eq.delete_profile(name)),
                     tr("Removed {profile}; EQ is off.").format(profile=name))
        else:
            eq.delete_profile(name)
            self.fill_profiles()
            self.update_enabled()
            self.say(tr("Removed {profile}.").format(profile=name), 4000)

    def search(self):
        query = self.search_edit.text().strip()
        if not query:
            return

        def show(hits):
            self.search_hits = hits[:300]
            self.results.clear()
            for h in self.search_hits:
                self.results.addItem(f"{h.name}    [{h.source}]")
            self.results.setVisible(bool(hits))
            self.download_btn.setVisible(bool(hits))
            if hits:
                self.results.setCurrentRow(0)
                if len(hits) > len(self.search_hits):
                    msg = tr("{count} profiles found (showing {shown}). Pick one and download it.")
                else:
                    msg = tr("{count} profiles found. Pick one and download it.")
                self.say(msg.format(count=len(hits), shown=len(self.search_hits)), 5000)
            else:
                self.say(tr("No AutoEQ profiles match “{query}”.").format(query=query), 6000, "warn")
        self.act(tr("Searching AutoEQ"), lambda: eq.search(query), on_done=show)

    def download(self):
        row = self.results.currentRow()
        if row < 0 or row >= len(self.search_hits):
            return
        entry = self.search_hits[row]

        def job():
            name, _ = eq.download(entry)
            if self.snap and self.snap.connected:
                eq.enable(name)
            else:
                eq.save_state(profile=name)
            return name
        self.act(tr("Downloading {profile}").format(profile=entry.name), job, tr("EQ on: {}."))

    def save_setting(self, **values):
        settings.set(**values)

    def pick_device(self, _index: int):
        value = self.device_combo.currentData()
        if not value or value == device.configured_id():
            return
        from . import actions
        self.act(tr("Switching device"), lambda: actions.use_device(value),
                 on_done=lambda msgs: self.say(" ".join(msgs), 8000, "ok"))

    def copy_diagnostics(self):
        from . import diagnostics

        def done(text):
            W.QApplication.clipboard().setText(text)
            self.say(tr("Diagnostics copied to the clipboard ({lines} lines). "
                        "Paste them into a bug report.").format(lines=len(text.splitlines())), 6000, "ok")
        self.act(tr("Collecting diagnostics"), diagnostics.report, on_done=done)

    # ---------------------------------------------------------------- window
    def present(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def closeEvent(self, event):
        if self.standalone:
            self.shutdown()
            event.accept()
        else:
            self.hide()       # the tray keeps running
            event.ignore()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.banner_timer.stop()
        # no more callbacks into this window while Qt tears its widgets down
        for w in self.findChildren(QtCore.QObject):
            w.blockSignals(True)
        if self.own_worker:
            self.worker.shutdown()


def main() -> int:
    app = make_app()
    w = Window()
    w.show()

    def cleanup():
        w.shutdown()
        app.quit()
    return run_app(app, cleanup)


if __name__ == "__main__":
    sys.exit(main())
