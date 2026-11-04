import pytest

from k7ctl import eq, eqcurve

PROFILE = """Preamp: -6.3 dB
Filter 1: ON LSC Fc 105 Hz Gain 6.5 dB Q 0.70
Filter 2: ON PK Fc 125 Hz Gain -2.7 dB Q 0.55
Filter 3: ON PK Fc 8445 Hz Gain 3.3 dB Q 1.61
Filter 4: ON HSC Fc 10000 Hz Gain -3.1 dB Q 0.70
Filter 5: ON LSC Fc 300 Hz Gain 4 dB Q 2.0
"""

# Measured through PipeWire's param_eq (tones on a null sink), see eqcurve docstring.
MEASURED = {40: 3.29, 105: -1.19, 200: -2.06, 300: -5.42, 600: -7.67, 1000: -6.76,
            3000: -6.21, 8445: -3.94, 12000: -7.67, 16000: -9.07}


def test_matches_pipewire_measurements():
    prof = eq.parse_profile(PROFILE)
    for hz, db in MEASURED.items():
        assert eqcurve.response(prof, [hz])[0][1] == pytest.approx(db, abs=0.05), hz


def test_shapes():
    def one(line, hz):
        return eqcurve.response(eq.parse_profile(line), [hz], include_preamp=False)[0][1]
    assert one("Filter: ON PK Fc 1000 Hz Gain 6 dB Q 1", 1000) == pytest.approx(6, abs=0.01)
    assert one("Filter: ON PK Fc 1000 Hz Gain 6 dB Q 1", 20) == pytest.approx(0, abs=0.05)
    assert one("Filter: ON LS Fc 200 Hz Gain -5 dB Q 0.7", 20) == pytest.approx(-5, abs=0.1)
    assert one("Filter: ON HS Fc 5000 Hz Gain 4 dB Q 0.7", 20000) == pytest.approx(4, abs=0.2)
    assert one("Filter: ON LP Fc 1000 Hz Q 0.707", 1000) == pytest.approx(-3.01, abs=0.05)
    assert one("Filter: ON HP Fc 100 Hz Q 0.707", 20) < -25


def test_preamp_and_frequencies():
    prof = eq.parse_profile("Preamp: -4 dB\nFilter: ON PK Fc 1000 Hz Gain 0 dB Q 1")
    assert eqcurve.response(prof, [500])[0][1] == pytest.approx(-4)
    f = eqcurve.frequencies(10)
    assert f[0] == pytest.approx(20) and f[-1] == pytest.approx(20000) and len(f) == 10
