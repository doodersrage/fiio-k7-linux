"""The K7's PipeWire sink: find it, read and set its software volume.

The K7 has no hardware volume over USB (the knob is analog), so PipeWire's
volume slider for it is software volume. Anything below 100% throws away
resolution before the DAC; for bit-perfect playback leave it at 100% and use
the K7's knob.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

USB_COMPONENT = "USB2972:0047"


@dataclass
class Sink:
    id: int
    name: str
    description: str
    volume: float | None
    muted: bool
    is_default: bool


def _run(args: list[str]) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_sinks(pw_dump: list) -> list[dict]:
    """The K7's Audio/Sink nodes from `pw-dump` JSON."""
    out = []
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        if USB_COMPONENT not in str(props.get("alsa.components", "")):
            continue
        out.append({
            "id": obj["id"],
            "name": props.get("node.name", ""),
            "description": props.get("node.description", props.get("node.name", "")),
        })
    return out


def parse_default_sink(pw_dump: list) -> str | None:
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        if ((obj.get("props") or {}).get("metadata.name")) != "default":
            continue
        for entry in obj.get("metadata") or []:
            if entry.get("key") == "default.audio.sink":
                value = entry.get("value")
                if isinstance(value, dict):
                    return value.get("name")
    return None


def parse_wpctl_volume(text: str) -> tuple[float | None, bool]:
    m = re.search(r"Volume:\s*([\d.]+)", text or "")
    vol = float(m.group(1)) if m else None
    return vol, "[MUTED]" in (text or "")


def find_sink() -> Sink | None:
    raw = _run(["pw-dump"])
    if not raw:
        return None
    try:
        dump = json.loads(raw)
    except ValueError:
        return None
    sinks = parse_sinks(dump)
    if not sinks:
        return None
    default = parse_default_sink(dump)
    s = next((s for s in sinks if s["name"] == default), sinks[0])
    vol, muted = parse_wpctl_volume(_run(["wpctl", "get-volume", str(s["id"])]) or "")
    return Sink(id=s["id"], name=s["name"], description=s["description"],
                volume=vol, muted=muted, is_default=s["name"] == default)


def set_full_volume(sink: Sink) -> bool:
    ok1 = _run(["wpctl", "set-volume", str(sink.id), "1.0"]) is not None
    ok2 = _run(["wpctl", "set-mute", str(sink.id), "0"]) is not None
    return ok1 and ok2


def make_default(sink: Sink) -> bool:
    return _run(["wpctl", "set-default", str(sink.id)]) is not None
