import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("k7ctl.tray")

from k7ctl import device, pipewire, snapshot  # noqa: E402


def snap(k7=None, sink=None, **kw):
    base = dict(allowed_rates=[48000], latency="default", quantum=1024, keepawake=False,
                keepawake_config=False, eq_profile=None, eq_on=False)
    base.update(kw)
    return snapshot.Snapshot(k7=k7, sink=sink, **base)


def test_tray_states(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    current = {"s": snap()}
    monkeypatch.setattr(qt.snapshot, "take", lambda: current["s"])

    app = qt.QApplication.instance() or qt.QApplication([])
    t = qt.Tray(app)
    assert t.a_status.text() == "No FiiO K7 connected"
    assert not t.a_rates.isChecked()
    assert t.lat_actions["default"].isChecked()

    k7 = device.K7(1, "K7", "FiiO K7", firmware="1.22",
                   stream=device.Stream("running", "S32_LE", 2, 48000))
    sink = pipewire.Sink(53, "k7", "FiiO K7", 0.5, False, True, rate=48000, running=True,
                         streams=[pipewire.Stream(1, "Firefox", 48000, "F32LE", True),
                                  pipewire.Stream(2, "Spotify", 44100, "F32LE", True)])
    current["s"] = snap(k7, sink, allowed_rates=[44100, 48000], keepawake=True)
    t.refresh()
    assert t.a_status.text() == "FiiO K7: 48 kHz / 32-bit"
    assert "50%" in t.a_detail.text()
    assert t.a_warn.isVisible() and "Spotify" in t.a_warn.text() and "Firefox" in t.a_warn.text()
    assert t.a_volume.isEnabled() and t.a_rates.isChecked() and t.a_awake.isChecked()

    t.build_eq_menu()
    labels = [a.text() for a in t.m_eq.actions()]
    assert labels[0].startswith("Off") and labels[-1].startswith("Find")

    t.toggle_autostart(True)
    assert qt.autostart_path().read_text().startswith("[Desktop Entry]")
    t.toggle_autostart(False)
    assert not qt.autostart_path().exists()
    t.timer.stop()
