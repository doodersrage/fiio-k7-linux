import pytest

qt = pytest.importorskip("k7ctl.qt")
from k7ctl import device, pipewire, snapshot, tray  # noqa: E402


def snap(k7=None, sink=None, **kw):
    base = dict(allowed_rates=[48000], latency="default", quantum=1024, keepawake=False,
                keepawake_config=False, eq_profile=None, eq_on=False)
    base.update(kw)
    return snapshot.Snapshot(k7=k7, sink=sink, **base)


def playing_snap():
    k7 = device.K7(1, "K7", "FiiO K7", firmware="1.22",
                   stream=device.Stream("running", "S32_LE", 2, 48000))
    sink = pipewire.Sink(53, "k7", "FiiO K7", 0.5, False, True, rate=48000, running=True,
                         streams=[pipewire.Stream(1, "Firefox", 48000, "F32LE", True),
                                  pipewire.Stream(2, "Spotify", 44100, "F32LE", True)])
    return snap(k7, sink, allowed_rates=[44100, 48000], keepawake=True)


def test_tray_states(monkeypatch, xdg, sync_worker):
    current = {"s": snap()}
    monkeypatch.setattr(snapshot, "take", lambda *a, **k: current["s"])
    app = qt.make_app()
    t = tray.Tray(app, sync_worker)
    assert t.a_status.text() == "No FiiO K7 connected"
    assert not t.a_rates.isChecked()
    assert t.lat_actions["default"].isChecked()

    current["s"] = playing_snap()
    t.refresh()
    assert t.a_status.text() == "FiiO K7: 48 kHz / 32-bit"
    assert "50%" in t.a_detail.text()
    assert t.a_warn.isVisible() and "Spotify" in t.a_warn.text() and "Firefox" in t.a_warn.text()
    assert t.a_rates.isChecked() and t.a_awake.isChecked()

    t.build_eq_menu()
    labels = [a.text() for a in t.m_eq.actions()]
    assert labels[0].startswith("Off") and labels[-1].startswith("Find")

    t.toggle_autostart(True)
    assert tray.autostart_path().read_text().startswith("[Desktop Entry]")
    t.toggle_autostart(False)
    assert not tray.autostart_path().exists()

    # clicking opens the window, sharing the worker
    t.open_window()
    assert t.window.isVisible() and t.window.worker is sync_worker
    t.window.close()                      # not standalone: hides, tray keeps running
    assert not t.window.isVisible() and not t.window.closed
    t.shutdown()


def test_tray_actions_run_on_worker(monkeypatch, xdg, sync_worker):
    monkeypatch.setattr(snapshot, "take", lambda *a, **k: playing_snap())
    calls = []
    monkeypatch.setattr(tray.rates, "disable", lambda: calls.append("rates-off"))
    app = qt.make_app()
    t = tray.Tray(app, sync_worker)
    n = len(sync_worker.jobs)
    t.a_rates.setChecked(False)
    assert calls == ["rates-off"] and len(sync_worker.jobs) > n
    t.shutdown()


def test_tray_notifications(monkeypatch, xdg, sync_worker):
    from k7ctl import settings
    settings.set(notify_rate=True, notify_resample=True)
    current = {"s": snap()}
    monkeypatch.setattr(snapshot, "take", lambda *a, **k: current["s"])
    app = qt.make_app()
    t = tray.Tray(app, sync_worker)
    shown = []
    monkeypatch.setattr(t.tray, "showMessage", lambda title, text, *a: shown.append(text))

    def playing(rate, streams):
        k7 = device.K7(1, "K7", "FiiO K7", stream=device.Stream("running", "S32_LE", 2, rate))
        sink = pipewire.Sink(53, "k7", "FiiO K7", 1.0, False, True, rate=rate, running=True,
                             streams=streams)
        return snap(k7, sink, allowed_rates=[44100, 48000])

    current["s"] = playing(44100, [pipewire.Stream(1, "Strawberry", 44100, "S32LE", True)])
    t.refresh()
    current["s"] = playing(48000, [pipewire.Stream(2, "Firefox", 48000, "F32LE", True),
                                   pipewire.Stream(1, "Strawberry", 44100, "S32LE", True)])
    t.refresh()
    assert any("Now playing 48 kHz" in m for m in shown)
    assert any("Strawberry is 44.1 kHz" in m for m in shown)
    n = len(shown)
    t.refresh()                                   # nothing changed: no repeat
    assert len(shown) == n
    assert t.a_n_rate.isChecked() and t.a_n_res.isChecked()
    t.shutdown()
