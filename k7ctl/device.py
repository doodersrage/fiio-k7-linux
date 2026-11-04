"""Find the DAC (the FiiO K7 by default) and read what it is doing from the kernel.

The K7 is a USB Audio Class 2 device. Its only USB interfaces are audio
streaming and firmware update (DFU): it has no volume or mute controls and no
vendor control channel. Everything here is read-only and comes from /proc/asound
and /sys, so no special permissions are needed.

Other USB DACs work too: `k7ctl use <vid:pid>` (or K7CTL_DEVICE=vid:pid) picks
one; "auto" (the default) means the FiiO K7.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# USB ids k7ctl knows by name; "auto" looks for these
KNOWN = {"2972:0047": "FiiO K7"}
DEFAULT_ID = "2972:0047"

PROC_ASOUND = Path("/proc/asound")
SYS_USB = Path("/sys/bus/usb/devices")


@dataclass
class Stream:
    """What the kernel is currently sending to the DAC."""

    state: str = "closed"          # closed | open | running
    format: str | None = None      # e.g. S32_LE, DSD_U32_BE
    channels: int | None = None
    rate: int | None = None        # Hz requested by the player / sound server
    momentary_rate: int | None = None  # Hz measured from the DAC's feedback

    @property
    def is_dsd(self) -> bool:
        return bool(self.format and self.format.startswith("DSD"))

    def describe(self) -> str:
        if self.state == "closed" or not self.rate:
            return "idle"
        bits = format_bits(self.format)
        if self.is_dsd:
            # DSD_U32 carries 32 one-bit samples per channel per frame
            dsd_rate = self.rate * 32
            return f"DSD{dsd_rate // 44100} ({dsd_rate / 1e6:.4g} MHz, native)"
        text = f"{format_rate(self.rate)} / {bits}-bit" if bits else format_rate(self.rate)
        return text if self.state == "running" else f"{text} (paused)"


@dataclass
class K7:
    """The DAC k7ctl is working with (the FiiO K7, or another USB DAC)."""
    card_index: int
    card_id: str              # ALSA id, e.g. "K7" -> hw:K7
    name: str
    usb_id: str = DEFAULT_ID  # "vvvv:pppp"
    usb_path: str | None = None
    firmware: str | None = None   # from bcdDevice, e.g. "1.22"
    usb_speed: str | None = None
    supported_rates: list[int] = field(default_factory=list)
    supported_formats: list[str] = field(default_factory=list)
    stream: Stream = field(default_factory=Stream)

    @property
    def is_k7(self) -> bool:
        return self.usb_id == DEFAULT_ID

    @property
    def alsa_device(self) -> str:
        return f"hw:CARD={self.card_id},DEV=0"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["alsa_device"] = self.alsa_device
        d["stream"]["description"] = self.stream.describe()
        return d


# ---------------------------------------------------------------- formatting

def format_rate(hz: int) -> str:
    khz = hz / 1000
    return f"{khz:g} kHz"


def format_bits(fmt: str | None) -> int | None:
    if not fmt:
        return None
    m = re.match(r"[SU](\d+)", fmt)
    if not m:
        return None
    bits = int(m.group(1))
    # S24_3LE etc. are 24-bit; S32_LE on this DAC is a 32-bit container
    return bits


def firmware_from_bcd(bcd: str) -> str:
    """'0122' -> '1.22' (bcdDevice is binary-coded decimal)."""
    bcd = bcd.strip().rjust(4, "0")
    return f"{int(bcd[:2])}.{bcd[2:]}"


# ------------------------------------------------------------------- parsing

def parse_hw_params(text: str) -> dict:
    """Parse /proc/asound/cardN/pcm0p/sub0/hw_params."""
    text = text.strip()
    if not text or text == "closed":
        return {}
    out: dict = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "format":
            out["format"] = value
        elif key == "channels":
            out["channels"] = int(value)
        elif key == "rate":
            out["rate"] = int(value.split()[0])
    return out


def parse_stream0(text: str) -> dict:
    """Parse /proc/asound/cardN/stream0 (playback section only)."""
    out: dict = {"running": False, "momentary_rate": None, "rates": [], "formats": []}
    section = text.split("Capture:")[0]
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("Status:"):
            out["running"] = line.split(":", 1)[1].strip() == "Running"
        elif line.startswith("Momentary freq"):
            m = re.search(r"=\s*(\d+)\s*Hz", line)
            if m:
                out["momentary_rate"] = int(m.group(1))
        elif line.startswith("Rates:"):
            for r in line.split(":", 1)[1].split(","):
                r = r.strip()
                if r.isdigit() and int(r) not in out["rates"]:
                    out["rates"].append(int(r))
        elif line.startswith("Format:"):
            f = line.split(":", 1)[1].strip()
            if f not in out["formats"]:
                out["formats"].append(f)
    out["rates"].sort()
    return out


def parse_pcm_status(text: str) -> str:
    """'closed', 'open' (prepared/paused/setup) or 'running'."""
    text = text.strip()
    if not text or text == "closed":
        return "closed"
    m = re.search(r"state:\s*(\w+)", text)
    if m and m.group(1) == "RUNNING":
        return "running"
    return "open"


# ------------------------------------------------------------------- lookup

def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _find_usb_dir(sys_usb: Path, usb_id: str) -> Path | None:
    vid, pid = usb_id.split(":")
    try:
        entries = list(sys_usb.iterdir())
    except OSError:
        return None
    for d in entries:
        if _read(d / "idVendor").strip() == vid and _read(d / "idProduct").strip() == pid:
            return d
    return None


def normalize_id(value: str) -> str:
    """'2972:0047' / '0x2972:0x0047' / 'auto' -> canonical form; ValueError if invalid."""
    v = value.strip().lower()
    if v in ("", "auto"):
        return "auto"
    m = re.fullmatch(r"(?:0x)?([0-9a-f]{1,4}):(?:0x)?([0-9a-f]{1,4})", v)
    if not m:
        raise ValueError(f"not a USB id: {value!r} (expected vvvv:pppp, e.g. 2972:0047, or auto)")
    return f"{int(m.group(1), 16):04x}:{int(m.group(2), 16):04x}"


def configured_id() -> str:
    """'auto' or the USB id chosen with `k7ctl use` / K7CTL_DEVICE."""
    env = os.environ.get("K7CTL_DEVICE")
    if env:
        try:
            return normalize_id(env)
        except ValueError:
            pass
    from . import settings
    try:
        return normalize_id(str(settings.get("device")))
    except ValueError:
        return "auto"


def target_id() -> str:
    """The USB id to look for: the configured one, or the K7 in auto mode."""
    c = configured_id()
    return DEFAULT_ID if c == "auto" else c


@dataclass
class UsbCard:
    card_index: int
    card_id: str
    usb_id: str
    name: str
    playback: bool


def _card_name(card: Path, usb: Path | None, usb_id: str) -> str:
    if usb_id in KNOWN:
        return KNOWN[usb_id]
    if usb is not None:
        maker = _read(usb / "manufacturer").strip()
        product = _read(usb / "product").strip()
        if product:
            return product if (not maker or product.startswith(maker.split()[0])) else f"{maker} {product}"
    return _read(card / "id").strip() or f"USB audio {usb_id}"


def list_usb_cards(proc: Path = PROC_ASOUND, sys_usb: Path = SYS_USB) -> list[UsbCard]:
    """Every USB sound card, for `k7ctl devices`."""
    out = []
    try:
        cards = sorted(proc.glob("card[0-9]*"), key=lambda p: int(p.name[4:]))
    except OSError:
        return out
    for card in cards:
        usbid = _read(card / "usbid").strip().lower()
        if ":" not in usbid:
            continue
        usb = _find_usb_dir(sys_usb, usbid)
        out.append(UsbCard(
            card_index=int(card.name[4:]),
            card_id=_read(card / "id").strip(),
            usb_id=usbid,
            name=_card_name(card, usb, usbid),
            playback=(card / "pcm0p").exists(),
        ))
    return out


def find(proc: Path = PROC_ASOUND, sys_usb: Path = SYS_USB,
         usb_id: str | None = None) -> K7 | None:
    """Return the DAC (the configured one, or the K7), or None if it isn't connected."""
    want = usb_id or target_id()
    try:
        cards = sorted(proc.glob("card[0-9]*"), key=lambda p: int(p.name[4:]))
    except OSError:
        return None
    for card in cards:
        usbid = _read(card / "usbid").strip().lower()
        if usbid != want:
            continue
        usb = _find_usb_dir(sys_usb, usbid)
        k7 = K7(
            card_index=int(card.name[4:]),
            card_id=_read(card / "id").strip() or "USB",
            name=_card_name(card, usb, usbid),
            usb_id=usbid,
        )
        if usb is not None:
            k7.usb_path = os.fspath(usb)
            bcd = _read(usb / "bcdDevice").strip()
            if bcd:
                k7.firmware = firmware_from_bcd(bcd)
            speed = _read(usb / "speed").strip()
            if speed:
                k7.usb_speed = f"{speed} Mbit/s"

        s0 = parse_stream0(_read(card / "stream0"))
        k7.supported_rates = s0["rates"]
        k7.supported_formats = s0["formats"]

        sub = card / "pcm0p" / "sub0"
        hw = parse_hw_params(_read(sub / "hw_params"))
        state = parse_pcm_status(_read(sub / "status"))
        if state == "closed" and s0["running"]:
            state = "running"
        k7.stream = Stream(
            state=state if hw else "closed",
            format=hw.get("format"),
            channels=hw.get("channels"),
            rate=hw.get("rate"),
            momentary_rate=s0["momentary_rate"],
        )
        return k7
    return None


def display_name() -> str:
    """Name for messages when the device may not be connected."""
    t = target_id()
    return KNOWN.get(t, f"USB DAC {t}")
