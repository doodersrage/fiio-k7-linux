import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qt = pytest.importorskip("k7ctl.tray")

from k7ctl import device, pipewire  # noqa: E402


def test_tray_states(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    state = {"k7": None}
    monkeypatch.setattr(qt.device, "find", lambda: state["k7"])
    monkeypatch.setattr(qt.pipewire, "find_sink", lambda: pipewire.Sink(
        53, "k7", "FiiO K7", 0.5, False, True))
    monkeypatch.setattr(qt.rates, "live_allowed_rates", lambda: [48000])

    app = qt.QApplication.instance() or qt.QApplication([])
    t = qt.Tray(app)
    assert t.a_status.text() == "No FiiO K7 connected"
    assert not t.a_rates.isChecked()

    state["k7"] = device.K7(1, "K7", "FiiO K7", firmware="1.22",
                            stream=device.Stream("running", "S32_LE", 2, 44100))
    t.refresh()
    assert t.a_status.text() == "FiiO K7: 44.1 kHz / 32-bit"
    assert "50%" in t.a_detail.text() and "1.22" in t.a_detail.text()
    assert t.a_volume.isEnabled()

    t.toggle_autostart(True)
    assert qt.autostart_path().read_text().startswith("[Desktop Entry]")
    t.toggle_autostart(False)
    assert not qt.autostart_path().exists()
    t.timer.stop()
