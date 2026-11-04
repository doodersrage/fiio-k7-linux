from k7ctl import keepawake, latency, pipewire, pwconf, snapshot
from k7ctl.device import K7, Stream as DevStream
from k7ctl.pipewire import Sink, Stream

SETTINGS = """\
update: id:0 key:'clock.quantum' value:'2048' type:''
update: id:0 key:'clock.min-quantum' value:'1024' type:''
update: id:0 key:'clock.max-quantum' value:'8192' type:''
"""


def test_latency_current():
    s = pwconf.parse_settings(SETTINGS)
    assert latency.current(s) == "stable"
    s["clock.quantum"] = "999"
    assert latency.current(s) == "custom"
    assert latency.current({}) is None


def test_latency_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(pwconf, "set_setting", lambda k, v: calls.append((k, v)) or True)
    assert latency.apply("stable")
    assert "default.clock.quantum = 2048" in latency.conf_path().read_text()
    assert calls[-1] == ("clock.quantum", "2048")
    latency.apply("default")
    assert not latency.conf_path().exists()


def test_keepawake_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path, restarted = keepawake.enable(restart=False)
    text = path.read_text()
    assert not restarted and "session.suspend-timeout-seconds = 0" in text
    assert 'alsa.components = "~.*USB2972:0047.*"' in text
    assert keepawake.configured_for() == "2972:0047"
    assert "wireplumber.conf.d" in str(path)
    assert keepawake.disable(restart=False) == (True, False)
    dump = [{"type": "PipeWire:Interface:Node", "id": 1, "info": {"props": {
        "media.class": "Audio/Sink", "node.name": "alsa_output.usb-X_FiiO_K7-00.analog-stereo",
        "alsa.components": "USB2972:0047", "session.suspend-timeout-seconds": 0}}}]
    assert keepawake.active_on_node(dump) is True



def test_k7_is_default():
    def d(default):
        return [
            {"id": 53, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Audio/Sink", "alsa.components": "USB2972:0047", "node.name": "k7"}}},
            {"id": 9, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
             "metadata": [{"key": "default.audio.sink", "value": {"name": default}}]},
        ]
    assert pipewire.k7_is_default(d("k7"))
    assert pipewire.k7_is_default(d(pipewire.EQ_NODE))
    assert not pipewire.k7_is_default(d("bt-w5"))


def _snap(sink, **kw):
    k7 = K7(1, "K7", "FiiO K7", stream=DevStream("running", "S32_LE", 2, sink.rate))
    base = dict(allowed_rates=[44100, 48000], latency="default", quantum=1024,
                keepawake=False, keepawake_config=False, eq_profile=None, eq_on=False)
    base.update(kw)
    return snapshot.Snapshot(k7=k7, sink=sink, **base)


def test_bit_perfect_and_why():
    ok = Sink(1, "k7", "K7", 1.0, False, True, rate=44100, running=True,
              streams=[Stream(2, "Strawberry", 44100, "S32LE", True)])
    s = _snap(ok)
    assert s.bit_perfect is True and s.short() == "44.1k" and s.css_class() == "playing"

    mixed = Sink(1, "k7", "K7", 1.0, False, True, rate=48000, running=True,
                 streams=[Stream(2, "Firefox", 48000, "F32LE", True),
                          Stream(3, "Strawberry", 44100, "S32LE", True),
                          Stream(4, "Idle tab", 44100, "F32LE", False)])
    s = _snap(mixed)
    assert s.bit_perfect is False
    assert [x.app for x in s.resampled] == ["Strawberry"]
    assert s.why_resampled() == ["Strawberry is 44.1 kHz but the K7 is running at 48 kHz "
                                 "because Firefox started first"]
    assert s.short() == "48k*" and s.css_class() == "resampled"

    s = _snap(mixed, allowed_rates=[48000])
    assert "only allows one rate" in s.why_resampled()[0]

    quiet = Sink(1, "k7", "K7", 0.4, False, True, rate=44100, running=True)
    assert _snap(quiet).bit_perfect is False
    assert _snap(ok, eq_on=True, eq_profile="HD 600").short() == "44.1k EQ"
