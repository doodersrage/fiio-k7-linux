import pytest

from k7ctl import exclusive, pipewire


def dump(profile="output:analog-stereo", default="k7"):
    return [
        {"id": 129, "type": "PipeWire:Interface:Device", "info": {
            "props": {"alsa.components": "USB2972:0047", "device.name": "alsa_card.k7"},
            "params": {
                "EnumProfile": [{"index": 0, "name": "off"}, {"index": 1, "name": "output:analog-stereo"},
                                {"index": 4, "name": "pro-audio"}],
                "Profile": [{"index": 0 if profile == "off" else 1, "name": profile}]}}},
        {"id": 53, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "alsa.components": "USB2972:0047", "node.name": "k7"}}},
        {"id": 9, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
         "metadata": [{"key": "default.audio.sink", "value": {"name": default}}]},
    ]


def test_find_device():
    dev = exclusive.find_device(dump())
    assert dev == {"id": 129, "profile": "output:analog-stereo",
                   "profiles": {"off": 0, "output:analog-stereo": 1, "pro-audio": 4}}
    assert exclusive.is_on(dump("off")) and not exclusive.is_on(dump())


def test_enable_disable_roundtrip(xdg, monkeypatch):
    current = {"d": dump()}
    calls = []
    monkeypatch.setattr(pipewire, "dump", lambda: current["d"])
    monkeypatch.setattr(exclusive, "_set_profile", lambda dev, idx: calls.append((dev, idx)) or True)
    monkeypatch.setattr(pipewire, "wait_and_set_default", lambda *a, **k: calls.append("default") or True)
    monkeypatch.setattr(exclusive, "_alsa_hint", lambda: "hw:CARD=K7,DEV=0")

    assert "released" in exclusive.enable()
    assert calls == [(129, 0)]
    assert exclusive._load() == {"profile": "output:analog-stereo", "was_default": True}

    current["d"] = dump("off")
    assert exclusive.enable() == "Already released."
    assert "again" in exclusive.disable()
    assert calls[-2:] == [(129, 1), "default"]           # previous profile, then default output
    assert not exclusive.state_path().exists()


def test_disable_without_state_falls_back_to_analog(xdg, monkeypatch):
    calls = []
    monkeypatch.setattr(pipewire, "dump", lambda: dump("off", default="bt"))
    monkeypatch.setattr(exclusive, "_set_profile", lambda dev, idx: calls.append(idx) or True)
    monkeypatch.setattr(pipewire, "wait_and_set_default", lambda *a, **k: True)
    exclusive.disable()
    assert calls == [1]


def test_errors(monkeypatch):
    monkeypatch.setattr(pipewire, "dump", lambda: [])
    with pytest.raises(RuntimeError):
        exclusive.enable()


def test_direct_user(tmp_path):
    proc = tmp_path
    st = proc / "asound" / "card1" / "pcm0p" / "sub0"
    st.mkdir(parents=True)
    (st / "status").write_text("state: RUNNING\nowner_pid   : 4242\n")
    (proc / "4242").mkdir()
    (proc / "4242" / "comm").write_text("mpd\n")
    assert exclusive.direct_user(1, proc) == "mpd (pid 4242)"
    (proc / "4242" / "comm").write_text("pipewire\n")
    assert exclusive.direct_user(1, proc) is None
    (st / "status").write_text("closed\n")
    assert exclusive.direct_user(1, proc) is None
