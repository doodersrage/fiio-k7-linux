import pytest

qt = pytest.importorskip("k7ctl.qt")
from k7ctl import device, eq, pipewire, snapshot, window  # noqa: E402

HD600 = "Preamp: -6.3 dB\nFilter 1: ON PK Fc 125 Hz Gain -2.7 dB Q 0.55\n"


def make_snap(connected=True, **kw):
    if not connected:
        k7 = sink = None
    else:
        k7 = device.K7(1, "K7", "FiiO K7", firmware="1.22", usb_speed="480 Mbit/s",
                       supported_rates=[44100, 384000], supported_formats=["S32_LE", "DSD_U32_BE"],
                       stream=device.Stream("running", "S32_LE", 2, 48000))
        sink = pipewire.Sink(53, "k7", "FiiO K7", 1.0, False, True, rate=48000, running=True,
                             streams=[pipewire.Stream(1, "Firefox", 48000, "F32LE", True),
                                      pipewire.Stream(2, "Strawberry", 44100, "S32LE", True)])
    base = dict(allowed_rates=[44100, 48000], latency="stable", quantum=2048, keepawake=False,
                keepawake_config=False, eq_profile=None, eq_on=False)
    base.update(kw)
    return snapshot.Snapshot(k7=k7, sink=sink, **base)


@pytest.fixture
def win(monkeypatch, xdg, sync_worker):
    current = {"s": make_snap()}
    monkeypatch.setattr(snapshot, "take", lambda *a, **k: current["s"])
    qt.make_app()
    w = window.Window(sync_worker)
    w.current = current
    yield w
    w.shutdown()


def test_shows_status_and_resampling(win):
    assert "Playing 48 kHz / 32-bit" in win.subtitle.text()
    assert win.bitperfect.text() == "No: resampled"
    assert win.streams.rowCount() == 2
    assert win.streams.item(1, 2).text() == "resampled to 48 kHz"
    assert win.why.isVisibleTo(win) and "Firefox started first" in win.why.text()
    assert win.rates_cb.isChecked()
    assert win.latency_combo.currentData() == "stable"
    assert win.dev["firmware"].text() == "1.22"
    assert "native DSD" in win.dev["formats"].text()
    assert not win.volume_btn.isEnabled()       # already 100%


def test_disconnected(win):
    win.current["s"] = make_snap(connected=False, allowed_rates=[48000])
    win.refresh()
    assert win.subtitle.text().startswith("Not connected")
    assert win.streams.rowCount() == 0
    assert not win.awake_cb.isEnabled() and not win.eq_cb.isEnabled()


def test_settings_actions(win, monkeypatch):
    calls = []
    monkeypatch.setattr(window.keepawake, "enable", lambda: calls.append("awake"))
    monkeypatch.setattr(window.latency, "apply", lambda n: calls.append(("latency", n)))
    win.awake_cb.setChecked(True)
    assert calls == ["awake"]
    assert "stays awake" in win.message()
    win.latency_combo.setCurrentIndex(win.latency_combo.findData("low-latency"))
    win.latency_combo.activated.emit(win.latency_combo.currentIndex())
    assert calls[-1] == ("latency", "low-latency")


def test_errors_are_shown_not_raised(win, monkeypatch):
    def boom():
        raise eq.EqError("no K7")
    monkeypatch.setattr(window.rates, "disable", boom)
    win.rates_cb.setChecked(False)
    assert "failed: no K7" in win.message()


def test_eq_search_download_and_toggle(win, monkeypatch):
    entries = [eq.AutoEqEntry("Sennheiser HD 600", "oratory1990/over-ear/Sennheiser%20HD%20600", "oratory1990")]
    monkeypatch.setattr(window.eq, "search", lambda q: entries)
    monkeypatch.setattr(eq, "_fetch", lambda url: HD600)
    enabled = []
    monkeypatch.setattr(window.eq, "enable", lambda name=None: enabled.append(name) or name)
    disabled = []
    monkeypatch.setattr(window.eq, "disable", lambda: disabled.append(True))

    assert win.profile_combo.currentData() is None
    win.search_edit.setText("hd 600")
    win.search()
    assert win.results.count() == 1 and win.results.isVisibleTo(win)
    win.download()
    assert enabled == ["Sennheiser HD 600"]
    assert eq.list_profiles() == ["Sennheiser HD 600"]
    assert win.profile_combo.currentData() == "Sennheiser HD 600"
    assert "EQ on: Sennheiser HD 600" in win.message()

    win.current["s"] = make_snap(eq_on=True, eq_profile="Sennheiser HD 600")
    win.refresh()
    assert win.eq_cb.isChecked() and "EQ: Sennheiser HD 600" in win.subtitle.text()
    assert win.bitperfect.text().startswith("No: EQ is on")
    win.eq_cb.setChecked(False)
    assert disabled == [True]


def test_eq_needs_a_profile(win):
    win.eq_cb.setEnabled(True)
    win.eq_cb.setChecked(True)
    assert not win.eq_cb.isChecked()
    assert "Find your headphones" in win.message()


def test_standalone_close_shuts_down(win):
    win.show()
    win.close()
    assert win.closed


def test_curve_device_picker_and_diagnostics(win, monkeypatch):
    eq.save_profile("HD 600", HD600)
    eq.save_state(profile="HD 600")
    win.fill_profiles()
    assert win.curve.points and win.curve.points[0][1] == pytest.approx(-6.3, abs=0.5)
    win.curve.resize(500, 170)
    win.curve.grab()                                  # paints without errors

    monkeypatch.setattr(window.device, "list_usb_cards", lambda: [
        window.device.UsbCard(1, "K7", "2972:0047", "FiiO K7", True),
        window.device.UsbCard(2, "BTW5", "041e:3130", "Creative BT-W5", True)])
    win.fill_devices()
    assert [win.device_combo.itemData(i) for i in range(win.device_combo.count())] == \
        ["auto", "2972:0047", "041e:3130"]
    used = []
    import k7ctl.actions as actions
    monkeypatch.setattr(actions, "use_device", lambda v: used.append(v) or [f"Using {v}."])
    win.device_combo.setCurrentIndex(2)
    win.device_combo.activated.emit(2)
    assert used == ["041e:3130"] and "Using 041e:3130" in win.message()

    import k7ctl.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, "report", lambda: "## k7ctl\nline\n")
    win.copy_diagnostics()
    assert qt.QtWidgets.QApplication.clipboard().text() == "## k7ctl\nline\n"
    assert "copied" in win.message()

    win.notify_rate_cb.setChecked(True)
    from k7ctl import settings
    assert settings.get("notify_rate") is True
