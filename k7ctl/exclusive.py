"""Hand the DAC to a player that uses it directly (e.g. MPD for native DSD).

PipeWire can't send DSD to the K7, so DSD needs a player that opens the K7's
ALSA device (hw:CARD=K7) itself. That fails with "Device or resource busy"
while PipeWire has the device open (always, with keep-awake on).

"Exclusive on" switches the DAC's PipeWire profile to "off": PipeWire closes the
device and its output disappears, so apps use another output meanwhile.
"Exclusive off" restores the previous profile and makes the DAC the default
output again if it was before. WirePlumber remembers profiles, so the DAC
stays released (also after replugging or a reboot) until exclusive is off.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import device, pipewire, pwconf

OFF = "off"


def state_path() -> Path:
    return pwconf.config_home() / "k7ctl" / "exclusive.json"


def _load() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(**kw) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kw, indent=2) + "\n")


def find_device(pw_dump: list, usb_id: str | None = None) -> dict | None:
    """The DAC's PipeWire Device: {'id', 'profile', 'profiles': {name: index}}."""
    component = pipewire.usb_component(usb_id).lower()
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Device":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if component not in str(props.get("alsa.components", "")).lower():
            continue
        params = info.get("params") or {}
        profiles = {p.get("name"): p.get("index") for p in params.get("EnumProfile") or []
                    if p.get("name") is not None}
        current = (params.get("Profile") or [{}])[0].get("name")
        return {"id": obj["id"], "profile": current, "profiles": profiles}
    return None


def is_on(pw_dump: list | None = None) -> bool:
    d = pw_dump if pw_dump is not None else pipewire.dump()
    dev = find_device(d) if d else None
    return bool(dev and dev["profile"] == OFF)


def _set_profile(dev_id: int, index: int) -> bool:
    return pipewire._run(["wpctl", "set-profile", str(dev_id), str(index)]) is not None


def enable() -> str:
    """Release the DAC. Returns a message; raises RuntimeError if it can't."""
    d = pipewire.dump()
    if not d:
        raise RuntimeError("PipeWire isn't running (pw-dump failed)")
    dev = find_device(d)
    if not dev:
        raise RuntimeError(f"{device.display_name()} isn't in PipeWire (is it connected?)")
    if dev["profile"] == OFF:
        return "Already released."
    if OFF not in dev["profiles"]:
        raise RuntimeError("the device has no 'off' profile")
    _save(profile=dev["profile"], was_default=pipewire.k7_is_default(d))
    if not _set_profile(dev["id"], dev["profiles"][OFF]):
        raise RuntimeError("wpctl set-profile failed")
    return (f"PipeWire released the {device.display_name()}. Players can now use "
            f"{_alsa_hint()} directly; other apps play elsewhere. Undo with: k7ctl exclusive off")


def disable() -> str:
    d = pipewire.dump()
    if not d:
        raise RuntimeError("PipeWire isn't running (pw-dump failed)")
    dev = find_device(d)
    if not dev:
        raise RuntimeError(f"{device.display_name()} isn't in PipeWire (is it connected?)")
    state = _load()
    if dev["profile"] != OFF:
        return "PipeWire is already using it."
    want = state.get("profile")
    if want not in dev["profiles"] or want == OFF:
        # fall back to the first real output profile
        want = next((n for n in dev["profiles"] if n.startswith("output:analog")),
                    next((n for n in dev["profiles"] if n != OFF), None))
    if want is None:
        raise RuntimeError("no profile to go back to")
    if not _set_profile(dev["id"], dev["profiles"][want]):
        raise RuntimeError("wpctl set-profile failed")
    if state.get("was_default", True):
        pipewire.wait_and_set_default(None, timeout=5)
    try:
        state_path().unlink()
    except OSError:
        pass
    return f"PipeWire is using the {device.display_name()} again."


def _alsa_hint() -> str:
    k7 = device.find()
    return k7.alsa_device if k7 else "its hw: device"


def direct_user(card_index: int, proc: Path = Path("/proc")) -> str | None:
    """Which program has the DAC's ALSA device open, if it isn't PipeWire."""
    status = proc / "asound" / f"card{card_index}" / "pcm0p" / "sub0" / "status"
    try:
        m = re.search(r"owner_pid\s*:\s*(\d+)", status.read_text())
    except OSError:
        return None
    if not m:
        return None
    pid = m.group(1)
    try:
        comm = (proc / pid / "comm").read_text().strip()
    except OSError:
        comm = "?"
    if comm.startswith(("pipewire", "wireplumber", "data-loop")):
        return None
    return f"{comm} (pid {pid})"

