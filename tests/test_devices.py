import pytest

from k7ctl import actions, cli, device, eq, keepawake, pipewire, settings


def _card(proc, sysusb, n, usb_id, card_id, product, maker="", playback=True):
    c = proc / f"card{n}"
    c.mkdir(parents=True)
    (c / "id").write_text(card_id + "\n")
    (c / "usbid").write_text(usb_id + "\n")
    (c / "stream0").write_text("Playback:\n  Status: Stop\n    Rates: 44100, 48000\n    Format: S16_LE\n")
    if playback:
        (c / "pcm0p" / "sub0").mkdir(parents=True)
        (c / "pcm0p" / "sub0" / "hw_params").write_text("closed\n")
        (c / "pcm0p" / "sub0" / "status").write_text("closed\n")
    d = sysusb / f"1-{n}"
    d.mkdir(parents=True)
    vid, pid = usb_id.split(":")
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")
    (d / "product").write_text(product + "\n")
    (d / "manufacturer").write_text(maker + "\n")
    (d / "bcdDevice").write_text("1000\n")


@pytest.fixture
def tree(tmp_path):
    proc, sysusb = tmp_path / "asound", tmp_path / "usb"
    _card(proc, sysusb, 1, "2972:0047", "K7", "FiiO K7", "GuangZhou FiiO Electronics Co.,Ltd")
    _card(proc, sysusb, 2, "041e:3130", "BTW5", "Creative BT-W5", "Creative")
    _card(proc, sysusb, 3, "046d:08e5", "C920", "HD Pro Webcam C920", "Logitech", playback=False)
    (proc / "card0").mkdir()          # a non-USB card
    return proc, sysusb


def test_normalize_id():
    assert device.normalize_id("2972:0047") == "2972:0047"
    assert device.normalize_id("0x41E:0x3130") == "041e:3130"
    assert device.normalize_id(" Auto ") == "auto"
    with pytest.raises(ValueError):
        device.normalize_id("fiio")


def test_list_and_find(tree, xdg, monkeypatch):
    proc, sysusb = tree
    cards = device.list_usb_cards(proc, sysusb)
    assert [(c.usb_id, c.name, c.playback) for c in cards] == [
        ("2972:0047", "FiiO K7", True), ("041e:3130", "Creative BT-W5", True),
        ("046d:08e5", "Logitech HD Pro Webcam C920", False)]
    k7 = device.find(proc, sysusb)
    assert k7.name == "FiiO K7" and k7.is_k7
    settings.set(device="041e:3130")
    other = device.find(proc, sysusb)
    assert other.name == "Creative BT-W5" and not other.is_k7 and other.firmware == "10.00"
    monkeypatch.setenv("K7CTL_DEVICE", "2972:0047")       # environment wins
    assert device.find(proc, sysusb).is_k7
    monkeypatch.setenv("K7CTL_DEVICE", "nonsense")         # ignored if invalid
    assert device.target_id() == "041e:3130"


def test_pipewire_matches_chosen_device(xdg):
    dump = [
        {"id": 1, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "alsa.components": "USB2972:0047", "node.name": "k7"}}},
        {"id": 2, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "alsa.components": "USB041e:3130", "node.name": "btw5"}}},
    ]
    assert [s["name"] for s in pipewire.parse_sinks(dump)] == ["k7"]
    settings.set(device="041e:3130")
    assert [s["name"] for s in pipewire.parse_sinks(dump)] == ["btw5"]
    assert "USB041e:3130" in keepawake.render_rule()


def test_use_device_moves_keepawake_and_eq(xdg, monkeypatch):
    calls = []
    keepawake.enable(restart=False)                       # rule for the K7
    monkeypatch.setattr(keepawake, "enable", lambda: calls.append("keepawake"))
    monkeypatch.setattr(eq, "is_running", lambda: True)
    monkeypatch.setattr(eq, "enable", lambda: calls.append("eq") or "P")
    monkeypatch.setattr(device, "find", lambda *a, **k: None)
    msgs = actions.use_device("041e:3130")
    assert settings.get("device") == "041e:3130"
    assert calls == ["keepawake", "eq"]
    assert "not connected" in msgs[0]
    with pytest.raises(ValueError):
        actions.use_device("bad")


def test_cli_devices_and_use(tree, xdg, monkeypatch, capsys):
    proc, sysusb = tree
    real_list, real_find = device.list_usb_cards, device.find
    monkeypatch.setattr(device, "list_usb_cards", lambda: real_list(proc, sysusb))
    monkeypatch.setattr(device, "find", lambda *a, **k: real_find(proc, sysusb))
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "* 2972:0047  FiiO K7" in out and "(no playback)" in out
    assert cli.main(["use", "041e:3130"]) == 0
    assert "Using Creative BT-W5" in capsys.readouterr().out
    assert cli.main(["use", "nope"]) == 1


def test_notify_flag(monkeypatch, xdg):
    sent = []
    monkeypatch.setattr(cli, "notify", lambda summary, body, error=False: sent.append((summary, body, error)) or True)
    monkeypatch.setattr(device, "find", lambda *a, **k: None)
    assert cli.main(["--notify", "firmware"]) == 1
    assert sent and sent[0][2] is True and "No FiiO K7 found" in sent[0][1]


def test_settings_roundtrip(xdg):
    assert settings.load()["device"] == "auto"
    settings.set(notify_rate=True)
    assert settings.get("notify_rate") is True
    with pytest.raises(KeyError):
        settings.set(bogus=1)
    settings.path().write_text("not json")
    assert settings.load()["notify_rate"] is False         # falls back to defaults
