import subprocess

import pytest

from k7ctl import eq, pipewire

HD600 = """\
Preamp: -6.3 dB
Filter 1: ON LSC Fc 105 Hz Gain 6.5 dB Q 0.70
Filter 2: ON PK Fc 125 Hz Gain -2.7 dB Q 0.55
Filter 3: OFF PK Fc 8445 Hz Gain 3.3 dB Q 1.61
Filter 4: ON HSC Fc 10000 Hz Gain -3.1 dB Q 0.70
"""

INDEX = """\
# Index
- [Sennheiser HD 600](./oratory1990/over-ear/Sennheiser%20HD%20600) by oratory1990
- [Sennheiser HD 600](./crinacle/GRAS%2043AG-7%20over-ear/Sennheiser%20HD%20600) by crinacle on GRAS 43AG-7
- [Sennheiser HD 650](./oratory1990/over-ear/Sennheiser%20HD%20650) by oratory1990
- [1MORE Aero (ANC Off)](./HypetheSonics/GRAS%20RA0045%20in-ear/1MORE%20Aero%20(ANC%20Off)) by HypetheSonics on GRAS RA0045
"""


def test_parse_profile():
    p = eq.parse_profile(HD600)
    assert p.preamp == -6.3
    assert [f.type for f in p.filters] == ["bq_lowshelf", "bq_peaking", "bq_highshelf"]
    assert p.filters[0].freq == 105 and p.filters[0].gain == 6.5 and p.filters[0].q == 0.7


def test_missing_preamp_gets_safe_cut():
    p = eq.parse_profile("Filter 1: ON PK Fc 1000 Hz Gain 4 dB Q 1\nFilter: ON LP Fc 18000 Hz")
    assert p.preamp is None and p.safe_preamp == -4.0
    assert p.filters[1].type == "bq_lowpass" and p.filters[1].q == eq.DEFAULT_Q


def test_parse_profile_rejects_garbage():
    with pytest.raises(eq.EqError):
        eq.parse_profile("hello\nworld")
    with pytest.raises(eq.EqError):
        eq.parse_profile("Filter 1: ON XYZ Fc 100 Hz Gain 1 dB Q 1")


def test_render_conf():
    text = eq.render_conf(eq.parse_profile(HD600), 'My "HD" 600', "alsa_output.k7")
    assert "type = bq_highshelf freq = 0 gain = -6.30" in text      # preamp first
    assert "type = bq_lowshelf freq = 105 gain = 6.50 q = 0.7" in text
    assert "8445" not in text                                       # OFF filter skipped
    assert 'target.object = "alsa_output.k7"' in text
    assert 'node.description = "FiiO K7 + EQ (My \\"HD\\" 600)"' in text
    assert text.count("{") == text.count("}") and text.count("[") == text.count("]")


@pytest.mark.skipif(subprocess.run(["sh", "-c", "command -v pipewire"],
                                   capture_output=True).returncode != 0,
                    reason="pipewire not installed")
def test_render_conf_parses(tmp_path):
    """PipeWire's own parser accepts the file (no daemon needed: just a config dump)."""
    conf = tmp_path / "eq.conf"
    conf.write_text(eq.render_conf(eq.parse_profile(HD600), "x", "y"))
    # pw-config parses the config; an error exits non-zero
    r = subprocess.run(["pw-config", "-n", str(conf), "list", "context.modules"],
                       capture_output=True, text=True)
    if r.returncode == 127:
        pytest.skip("pw-config not available")
    assert r.returncode == 0, r.stderr
    assert "bq_lowshelf" in r.stdout


def test_index_and_urls():
    entries = eq.parse_index(INDEX)
    assert len(entries) == 4
    assert entries[1].source == "crinacle on GRAS 43AG-7"
    assert entries[0].url == ("https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/"
                              "oratory1990/over-ear/Sennheiser%20HD%20600/"
                              "Sennheiser%20HD%20600%20ParametricEQ.txt")
    assert entries[3].url.endswith("/1MORE%20Aero%20%28ANC%20Off%29%20ParametricEQ.txt")
    hits = eq.search("hd 600", entries)
    assert [h.name for h in hits] == ["Sennheiser HD 600"] * 2
    assert eq.search("sennheiser hd 650", entries)[0].name == "Sennheiser HD 650"


def test_profiles_and_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    src = tmp_path / "Sennheiser HD 600 ParametricEQ.txt"
    src.write_text(HD600)
    name, path = eq.import_file(str(src))
    assert name == "Sennheiser HD 600" and path.is_file()
    assert eq.list_profiles() == ["Sennheiser HD 600"]
    assert eq.selected_profile() == "Sennheiser HD 600"      # only one: picked
    eq.save_profile("Other", HD600)
    assert eq.selected_profile() is None                      # ambiguous
    eq.save_state(profile="Other")
    assert eq.selected_profile() == "Other"
    with pytest.raises(eq.EqError):
        eq.save_profile("Bad", "not an eq file")
    assert eq.profile_path("a/b").name == "a-b.txt"
    assert eq.delete_profile("Other") and not eq.delete_profile("Other")


def test_daemon_follows_renamed_k7(tmp_path, monkeypatch):
    """When the K7's node name changes, the filter restarts pointed at the new name."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    eq.save_profile("P", HD600)

    names = iter([None, "k7.2", "k7.2", "k7", "STOP"])
    started, terminated = [], []

    class FakeChild:
        returncode = None

        def __init__(self, args):
            started.append((tmp_path / "run" / "k7ctl" / "eq.conf").read_text())

        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout=None):
            return 0

    def next_name():
        n = next(names)
        if n == "STOP":
            raise KeyboardInterrupt
        return n

    monkeypatch.setattr(eq, "_k7_sink_name", next_name)
    monkeypatch.setattr(subprocess, "Popen", FakeChild)
    monkeypatch.setattr(pipewire, "wait_and_set_default", lambda *a, **k: True)
    monkeypatch.setattr(eq.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        eq.run_daemon(poll=0.1)
    assert len(started) == 2
    assert 'target.object = "k7.2"' in started[0]
    assert 'target.object = "k7"' in started[1]
    assert len(terminated) >= 1
