import cmath
import math

import pytest

from k7ctl import cli, eq, resample, snapshot
from k7ctl.device import K7, Stream as DevStream
from k7ctl.pipewire import Sink


def response(coeffs, f, fs):
    b0, b1, b2, a0, a1, a2 = coeffs
    z = cmath.exp(-1j * 2 * math.pi * f / fs)
    return (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)


@pytest.mark.parametrize("preset", list(eq.CROSSFEED))
@pytest.mark.parametrize("fs", eq.CROSSFEED_RATES)
def test_crossfeed_keeps_centred_sound_flat(preset, fs):
    fc, feed = eq.CROSSFEED[preset]
    direct, cross = eq.crossfeed_coefficients(fc, feed, fs)
    for f in (20, 100, 300, 543, 842, 2000, 8000, 19000):
        mono = response(direct, f, fs) + response(cross, f, fs)
        assert abs(mono) == pytest.approx(1.0, abs=1e-9), f
    # one-sided bass reaches the other ear `feed` dB lower (measured on PipeWire: 4.7 / 6.2 / 9.7)
    sep = 20 * math.log10(abs(response(direct, 30, fs)) / abs(response(cross, 30, fs)))
    assert sep == pytest.approx(feed, abs=0.3)
    # and treble mostly doesn't
    assert 20 * math.log10(abs(response(cross, 5000, fs))) < -20


def test_render_conf_crossfeed_only_and_with_eq():
    cf = eq.render_conf(None, None, "k7", crossfeed="medium", quality=10)
    assert "label = bq_raw" in cf and "rate = 384000" in cf and "rate = 44100" in cf
    assert 'inputs = [ "cf_inL:In" "cf_inR:In" ]' in cf
    assert 'node.description = "FiiO K7 + crossfeed"' in cf
    assert cf.count("resample.quality = 10") == 2
    prof = eq.parse_profile("Preamp: -3 dB\nFilter 1: ON PK Fc 1000 Hz Gain 3 dB Q 1")
    both = eq.render_conf(prof, "HD 600", "k7", crossfeed="light")
    assert '{ output = "eq:Out 1" input = "cf_inL:In" }' in both
    assert 'inputs = [ "eq:In 1" "eq:In 2" ]' in both
    assert 'node.description = "FiiO K7 + EQ (HD 600) + crossfeed"' in both
    assert "resample.quality" not in both
    with pytest.raises(eq.EqError):
        eq.render_conf(None, None, "k7")
    with pytest.raises(eq.EqError):
        eq.render_conf(None, None, "k7", crossfeed="extreme")


def test_eq_and_crossfeed_are_independent(xdg, monkeypatch):
    calls = []
    monkeypatch.setattr(eq, "_start", lambda make_default=True: calls.append("start"))
    monkeypatch.setattr(eq, "_stop", lambda: calls.append("stop"))
    eq.save_profile("P", "Preamp: -1 dB\nFilter 1: ON PK Fc 100 Hz Gain 1 dB Q 1")
    assert eq.set_crossfeed("strong") == "strong" and calls[-1] == "start"
    eq.enable("P")
    eq.disable()                      # EQ off, crossfeed still on: keep running
    assert calls[-1] == "start" and eq.crossfeed_preset() == "strong"
    assert eq.set_crossfeed("off") is None and calls[-1] == "stop"
    with pytest.raises(eq.EqError):
        eq.set_crossfeed("extreme")


def test_resample_presets(xdg, monkeypatch):
    restarted = []
    monkeypatch.setattr(resample, "restart_pulse", lambda: restarted.append(True) or True)
    assert resample.current() == "default" and resample.quality() == 4
    assert resample.apply("high") is False and not restarted
    assert resample.quality() == 10 and resample.current() == "high"
    for p in resample.conf_paths():
        assert "resample.quality = 10" in p.read_text()
    assert resample.apply("max", restart=True) is True and restarted
    resample.apply("default")
    assert not any(p.exists() for p in resample.conf_paths())
    with pytest.raises(ValueError):
        resample.apply("ultra")


def test_snapshot_processing_flags():
    k7 = K7(1, "K7", "FiiO K7", stream=DevStream("running", "S32_LE", 2, 44100))
    sink = Sink(1, "k7", "K7", 1.0, False, True, rate=44100, running=True)
    base = dict(allowed_rates=[44100, 48000], latency="default", quantum=1024, keepawake=False,
                keepawake_config=False, eq_profile=None, eq_on=False)
    s = snapshot.Snapshot(k7=k7, sink=sink, crossfeed="light", **base)
    assert s.processing and s.bit_perfect is False and s.short() == "44.1k XF" and s.css_class() == "eq"


def test_cli_parses_new_commands():
    p = cli.build_parser()
    assert p.parse_args(["crossfeed", "medium"]).preset == "medium"
    assert p.parse_args(["crossfeed"]).preset is None
    a = p.parse_args(["resample", "high", "--restart"])
    assert a.preset == "high" and a.restart
    with pytest.raises(SystemExit):
        p.parse_args(["crossfeed", "extreme"])
