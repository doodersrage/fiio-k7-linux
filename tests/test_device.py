from pathlib import Path

from k7ctl import device, firmware

STREAM0_RUNNING = """\
GuangZhou FiiO Electronics Co.,Ltd FiiO K7 at usb-0000:12:00.0-4, high speed : USB Audio

Playback:
  Status: Running
    Interface = 1
    Altset = 1
    Packet Size = 72
    Momentary freq = 44101 Hz (0x5.8338)
    Feedback Format = 16.16
  Interface 1
    Altset 1
    Format: S32_LE
    Channels: 2
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
  Interface 1
    Altset 2
    Format: S16_LE
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
  Interface 1
    Altset 3
    Format: DSD_U32_BE
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
"""

STREAM0_STOPPED = STREAM0_RUNNING.replace("""  Status: Running
    Interface = 1
    Altset = 1
    Packet Size = 72
    Momentary freq = 44101 Hz (0x5.8338)
    Feedback Format = 16.16
""", "  Status: Stop\n")

HW_PARAMS = """\
access: MMAP_INTERLEAVED
format: S32_LE
subformat: STD
channels: 2
rate: 44100 (44100/1)
period_size: 512
buffer_size: 32768
"""

STATUS_RUNNING = "state: RUNNING\nowner_pid   : 2512\n"

# Interface 2 (DFU) + DFU functional descriptor, as read from the real K7
DFU_BLOB = bytes.fromhex("0904020000fe01010a" "092107fa0040001001")


def test_parse_stream0_running():
    s = device.parse_stream0(STREAM0_RUNNING)
    assert s["running"] is True
    assert s["momentary_rate"] == 44101
    assert s["rates"][0] == 44100 and s["rates"][-1] == 384000
    assert s["formats"] == ["S32_LE", "S16_LE", "DSD_U32_BE"]


def test_parse_stream0_stopped():
    s = device.parse_stream0(STREAM0_STOPPED)
    assert s["running"] is False
    assert s["momentary_rate"] is None


def test_parse_hw_params():
    assert device.parse_hw_params(HW_PARAMS) == {"format": "S32_LE", "channels": 2, "rate": 44100}
    assert device.parse_hw_params("closed\n") == {}


def test_parse_pcm_status():
    assert device.parse_pcm_status("closed") == "closed"
    assert device.parse_pcm_status(STATUS_RUNNING) == "running"
    assert device.parse_pcm_status("state: PAUSED\n") == "open"


def test_firmware_from_bcd():
    assert device.firmware_from_bcd("0122") == "1.22"
    assert device.firmware_from_bcd("1003") == "10.03"


def test_describe():
    assert device.Stream().describe() == "idle"
    assert device.Stream("running", "S32_LE", 2, 44100).describe() == "44.1 kHz / 32-bit"
    assert device.Stream("open", "S16_LE", 2, 96000).describe() == "96 kHz / 16-bit (paused)"
    # DSD64 = 2.8224 MHz = 88200 frames/s of 32 one-bit samples
    assert device.Stream("running", "DSD_U32_BE", 2, 88200).describe().startswith("DSD64")


def test_parse_dfu():
    d = firmware.parse_dfu(DFU_BLOB)
    assert d.can_download and d.can_upload and d.manifestation_tolerant
    assert not d.will_detach
    assert d.detach_timeout_ms == 250
    assert d.transfer_size == 64
    assert firmware.parse_dfu(b"\x09\x04\x00\x00\x00\x01\x01\x20\x03") is None


def _fake_tree(tmp_path: Path, running: bool) -> tuple[Path, Path]:
    proc, sysusb = tmp_path / "asound", tmp_path / "usb"
    other = proc / "card0"
    other.mkdir(parents=True)
    (other / "id").write_text("NVidia\n")
    card = proc / "card2"
    sub = card / "pcm0p" / "sub0"
    sub.mkdir(parents=True)
    (card / "id").write_text("K7\n")
    (card / "usbid").write_text("2972:0047\n")
    (card / "stream0").write_text(STREAM0_RUNNING if running else STREAM0_STOPPED)
    (sub / "hw_params").write_text(HW_PARAMS if running else "closed\n")
    (sub / "status").write_text(STATUS_RUNNING if running else "closed\n")
    dev = sysusb / "3-4"
    dev.mkdir(parents=True)
    (dev / "idVendor").write_text("2972\n")
    (dev / "idProduct").write_text("0047\n")
    (dev / "bcdDevice").write_text("0122\n")
    (dev / "speed").write_text("480\n")
    (dev / "descriptors").write_bytes(DFU_BLOB)
    return proc, sysusb


def test_find_playing(tmp_path):
    proc, sysusb = _fake_tree(tmp_path, running=True)
    k7 = device.find(proc, sysusb)
    assert k7.card_index == 2 and k7.card_id == "K7"
    assert k7.alsa_device == "hw:CARD=K7,DEV=0"
    assert k7.firmware == "1.22"
    assert k7.stream.state == "running"
    assert k7.stream.rate == 44100
    assert k7.stream.momentary_rate == 44101
    assert firmware.read_dfu(k7.usb_path).transfer_size == 64
    assert k7.to_dict()["stream"]["description"] == "44.1 kHz / 32-bit"


def test_find_idle(tmp_path):
    proc, sysusb = _fake_tree(tmp_path, running=False)
    k7 = device.find(proc, sysusb)
    assert k7.stream.state == "closed"
    assert k7.stream.describe() == "idle"


def test_find_none(tmp_path):
    (tmp_path / "card0").mkdir()
    assert device.find(tmp_path, tmp_path / "nope") is None
