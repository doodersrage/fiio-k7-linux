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


# ------------------------------------------------------------ automatic mode

def fake_proc(tmp_path, procs):
    for pid, (comm, argv0) in procs.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(argv0.encode() + b"\0--flag\0")
    (tmp_path / "self").mkdir()
    return tmp_path


def test_running_players(tmp_path):
    proc = fake_proc(tmp_path, {
        10: ("strawberry", "/usr/bin/strawberry"),
        11: ("python3", "/usr/bin/quodlibet"),            # found by argv[0]
        12: ("a-very-long-pla", "/opt/x/run"),            # comm cut to 15 characters
    })
    assert exclusive.running_players(["Strawberry", "mpd"], proc) == {"strawberry"}
    assert exclusive.running_players(["quodlibet"], proc) == {"quodlibet"}
    assert exclusive.running_players(["a-very-long-player"], proc) == {"a-very-long-player"}
    assert exclusive.running_players([], proc) == set()


def test_parse_players():
    assert exclusive.parse_players(" strawberry, mpd  MPD,,deadbeef ") == ["strawberry", "mpd", "deadbeef"]
    assert exclusive.parse_players("") == []


class FakeDac:
    """Exclusive mode as the watcher sees it, without PipeWire."""

    def __init__(self, monkeypatch, connected=True):
        self.on = False
        self.connected = connected
        self.log = []
        monkeypatch.setattr(exclusive, "is_on", lambda d=None: self.on)
        monkeypatch.setattr(exclusive, "enable", self.enable)
        monkeypatch.setattr(exclusive, "disable", self.disable)

    def enable(self, auto=False):
        if not self.connected:
            raise RuntimeError("not connected")
        self.on = True
        exclusive._save(profile="output:analog-stereo", **({"auto": True} if auto else {}))
        self.log.append("on" + (" (auto)" if auto else ""))
        return "released"

    def disable(self):
        self.on = False
        exclusive.state_path().unlink(missing_ok=True)
        self.log.append("off")
        return "back"


def test_watcher_releases_while_a_player_runs(xdg, monkeypatch):
    dac = FakeDac(monkeypatch)
    running = set()
    w = exclusive.Watcher(find=lambda names: running & set(names))
    players = ["strawberry", "mpd"]

    assert w.tick(players) is None and dac.log == []
    running.add("strawberry")
    assert "strawberry started" in w.tick(players)
    assert dac.log == ["on (auto)"] and exclusive.started_automatically()
    assert w.tick(players) is None                      # still running: nothing more
    running.add("mpd")
    running.discard("strawberry")
    assert w.tick(players) is None                      # another player took over
    running.clear()
    assert "again" in w.tick(players)
    assert dac.log == ["on (auto)", "off"]


def test_watcher_leaves_manual_choices_alone(xdg, monkeypatch):
    dac = FakeDac(monkeypatch)
    running = set()
    w = exclusive.Watcher(find=lambda names: running & set(names))
    players = ["mpd"]

    # turned on by hand: the watcher doesn't turn it off when the player closes
    dac.enable()
    running.add("mpd")
    w.tick(players)
    running.clear()
    w.tick(players)
    assert dac.on and dac.log == ["on"]

    # turned off by hand while the player runs: stays off until the player starts again
    dac.disable()
    running.add("mpd")
    w.tick(players)
    assert dac.on
    dac.disable()
    for _ in range(3):
        w.tick(players)
    assert not dac.on
    running.clear()
    w.tick(players)
    running.add("mpd")
    w.tick(players)
    assert dac.on


def test_watcher_waits_for_the_dac(xdg, monkeypatch):
    dac = FakeDac(monkeypatch, connected=False)
    w = exclusive.Watcher(find=lambda names: {"mpd"})
    assert w.tick(["mpd"]) is None and not dac.on
    dac.connected = True
    assert w.tick(["mpd"]) and dac.on


def test_set_auto_players(xdg, monkeypatch):
    from k7ctl import pwconf, settings
    calls = []
    monkeypatch.setattr(pwconf, "install_service", lambda name, unit: calls.append(("install", name, unit)) or True)
    monkeypatch.setattr(pwconf, "remove_service", lambda name: calls.append(("remove", name)))
    dac = FakeDac(monkeypatch)

    exclusive.set_auto_players(["strawberry"])
    assert settings.get("auto_exclusive") == ["strawberry"] == exclusive.auto_players()
    assert calls[0][:2] == ("install", exclusive.SERVICE) and "exclusive _watch" in calls[0][2]

    dac.enable(auto=True)
    exclusive.set_auto_players([])
    assert calls[-1] == ("remove", exclusive.SERVICE)
    assert exclusive.auto_players() == [] and not dac.on   # gave back what it had taken


def test_settings_default_list_isnt_shared(xdg):
    from k7ctl import settings
    settings.load()["auto_exclusive"].append("x")
    assert settings.load()["auto_exclusive"] == []
