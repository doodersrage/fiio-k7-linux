from k7ctl import pipewire, rates

META = """\
Found "settings" metadata 34
update: id:0 key:'log.level' value:'2' type:''
update: id:0 key:'clock.rate' value:'48000' type:''
update: id:0 key:'clock.allowed-rates' value:'[ 44100 48000 96000 ]' type:''
update: id:0 key:'clock.quantum' value:'1024' type:''
"""


def test_parse_allowed_rates():
    assert rates.parse_allowed_rates(META) == [44100, 48000, 96000]
    assert rates.parse_allowed_rates("nothing here") is None


def test_render_conf():
    text = rates.render_conf(rates.K7_RATES)
    assert "default.clock.allowed-rates = [ 44100 48000 88200 96000 176400 192000 352800 384000 ]" in text
    assert text.count("{") == text.count("}")


def test_enable_disable_writes_only_our_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(rates, "apply_live", lambda r: calls.append(r) or True)
    other = tmp_path / "pipewire" / "pipewire.conf.d" / "10-mine.conf"
    other.parent.mkdir(parents=True)
    other.write_text("keep me")

    path, live = rates.enable()
    assert live and path.is_file() and rates.is_enabled()
    assert calls[-1] == rates.K7_RATES

    removed, live = rates.disable()
    assert removed and not path.exists() and not rates.is_enabled()
    assert calls[-1] == rates.DEFAULT_RATES
    assert other.read_text() == "keep me"
    assert rates.disable(apply=False) == (False, False)


DUMP = [
    {"id": 53, "type": "PipeWire:Interface:Node", "info": {"props": {
        "media.class": "Audio/Sink", "alsa.components": "USB2972:0047",
        "node.name": "alsa_output.usb-FiiO_K7.analog-stereo", "node.description": "FiiO K7 Analog Stereo"}}},
    {"id": 79, "type": "PipeWire:Interface:Node", "info": {"props": {
        "media.class": "Audio/Sink", "alsa.components": "USB041e:3130",
        "node.name": "alsa_output.usb-Creative.analog-stereo"}}},
    {"id": 83, "type": "PipeWire:Interface:Device", "info": {"props": {"alsa.components": "USB2972:0047"}}},
    {"id": 40, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
     "metadata": [{"subject": 0, "key": "default.audio.sink",
                   "value": {"name": "alsa_output.usb-FiiO_K7.analog-stereo"}}]},
]


def test_parse_sinks_and_default():
    sinks = pipewire.parse_sinks(DUMP)
    assert [s["id"] for s in sinks] == [53]
    assert sinks[0]["description"] == "FiiO K7 Analog Stereo"
    assert pipewire.parse_default_sink(DUMP) == "alsa_output.usb-FiiO_K7.analog-stereo"


def test_parse_wpctl_volume():
    assert pipewire.parse_wpctl_volume("Volume: 1.00\n") == (1.0, False)
    assert pipewire.parse_wpctl_volume("Volume: 0.45 [MUTED]\n") == (0.45, True)
    assert pipewire.parse_wpctl_volume("") == (None, False)


def test_dsd_stream_is_not_resampled():
    dump = [
        {"id": 53, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
            "media.class": "Audio/Sink", "alsa.components": "USB2972:0047", "node.name": "k7"},
            "params": {"Format": [{"mediaSubtype": "dsd", "rate": 352800, "channels": 2}]}}},
        {"id": 97, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
            "application.name": "pw-dsdplay", "node.name": "pw-dsdplay"},
            "params": {"Format": [{"mediaSubtype": "dsd", "rate": 352800, "channels": 2}]}}},
        {"id": 200, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 97, "input-node-id": 53}},
    ]
    sink = pipewire.sink_from_dump(dump, with_volume=False)
    (s,) = sink.streams
    assert s.dsd and s.rate_text == "DSD64"
    assert sink.resampled() == []
    pcm = pipewire.Stream(1, "x", 44100, "S32LE", True)
    assert pcm.rate_text == "44.1 kHz"
