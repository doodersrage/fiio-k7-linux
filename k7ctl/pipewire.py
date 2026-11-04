"""The K7 in PipeWire: its sink, the streams playing to it, its volume.

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
from dataclasses import dataclass, field

USB_COMPONENT = "USB2972:0047"
EQ_NODE = "k7ctl_eq"


@dataclass
class Stream:
    """An app (or the EQ) sending audio to the K7."""
    id: int
    app: str
    rate: int | None
    format: str | None
    running: bool
    is_eq: bool = False


@dataclass
class Sink:
    id: int
    name: str
    description: str
    volume: float | None
    muted: bool
    is_default: bool
    rate: int | None = None          # rate the K7 is running at, if running
    running: bool = False
    streams: list[Stream] = field(default_factory=list)
    eq_active: bool = False          # the k7ctl EQ sink exists and feeds the K7
    eq_is_default: bool = False

    @property
    def playing(self) -> list[Stream]:
        return [s for s in self.streams if s.running]

    def resampled(self) -> list[Stream]:
        """Playing streams whose rate differs from the K7's."""
        if not self.rate:
            return []
        return [s for s in self.playing if s.rate and s.rate != self.rate]

    def holders(self) -> list[Stream]:
        """Playing streams already at the K7's rate (they decide the rate)."""
        if not self.rate:
            return []
        return [s for s in self.playing if s.rate == self.rate]


def _run(args: list[str]) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _props(obj: dict) -> dict:
    return (obj.get("info") or {}).get("props") or {}


def _format(obj: dict) -> dict:
    fmts = ((obj.get("info") or {}).get("params") or {}).get("Format") or []
    return fmts[0] if fmts else {}


def parse_sinks(pw_dump: list) -> list[dict]:
    """The K7's Audio/Sink nodes from `pw-dump` JSON."""
    out = []
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = _props(obj)
        if props.get("media.class") != "Audio/Sink":
            continue
        if USB_COMPONENT not in str(props.get("alsa.components", "")):
            continue
        fmt = _format(obj)
        out.append({
            "id": obj["id"],
            "name": props.get("node.name", ""),
            "description": props.get("node.description", props.get("node.name", "")),
            "rate": fmt.get("rate"),
            "running": (obj.get("info") or {}).get("state") == "running",
        })
    return out


def parse_streams(pw_dump: list, sink_id: int) -> list[Stream]:
    """Nodes linked into the K7 sink, with their own sample rate."""
    nodes = {o["id"]: o for o in pw_dump if o.get("type") == "PipeWire:Interface:Node"}
    seen: dict[int, Stream] = {}
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info") or {}
        if info.get("input-node-id") != sink_id:
            continue
        nid = info.get("output-node-id")
        node = nodes.get(nid)
        if node is None or nid in seen:
            continue
        props = _props(node)
        fmt = _format(node)
        name = str(props.get("node.name", ""))
        seen[nid] = Stream(
            id=nid,
            app=(props.get("application.name") or props.get("node.description")
                 or name or f"node {nid}"),
            rate=fmt.get("rate"),
            format=fmt.get("format"),
            running=(node.get("info") or {}).get("state") == "running",
            is_eq=name.startswith(EQ_NODE),
        )
    return list(seen.values())


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


def find_node_id(pw_dump: list, node_name: str) -> int | None:
    for obj in pw_dump:
        if obj.get("type") == "PipeWire:Interface:Node" and _props(obj).get("node.name") == node_name:
            return obj["id"]
    return None


def parse_wpctl_volume(text: str) -> tuple[float | None, bool]:
    m = re.search(r"Volume:\s*([\d.]+)", text or "")
    vol = float(m.group(1)) if m else None
    return vol, "[MUTED]" in (text or "")


def dump() -> list | None:
    raw = _run(["pw-dump"])
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def sink_from_dump(d: list, with_volume: bool = True) -> Sink | None:
    sinks = parse_sinks(d)
    if not sinks:
        return None
    default = parse_default_sink(d)
    s = next((s for s in sinks if s["name"] == default), sinks[0])
    vol, muted = (parse_wpctl_volume(_run(["wpctl", "get-volume", str(s["id"])]) or "")
                  if with_volume else (None, False))
    streams = parse_streams(d, s["id"])
    return Sink(id=s["id"], name=s["name"], description=s["description"],
                volume=vol, muted=muted, is_default=s["name"] == default,
                rate=s["rate"] if s["running"] else None, running=s["running"],
                streams=streams, eq_active=any(x.is_eq for x in streams),
                eq_is_default=default == EQ_NODE)


def find_sink() -> Sink | None:
    d = dump()
    return sink_from_dump(d) if d else None


def set_full_volume(sink: Sink) -> bool:
    ok1 = _run(["wpctl", "set-volume", str(sink.id), "1.0"]) is not None
    ok2 = _run(["wpctl", "set-mute", str(sink.id), "0"]) is not None
    return ok1 and ok2


def set_default(node_id: int) -> bool:
    return _run(["wpctl", "set-default", str(node_id)]) is not None


def make_default(sink: Sink) -> bool:
    return set_default(sink.id)


def k7_is_default(d: list) -> bool:
    """Is the K7 (or the k7ctl EQ in front of it) the default output?"""
    default = parse_default_sink(d)
    return bool(default) and (default == EQ_NODE
                              or any(s["name"] == default for s in parse_sinks(d)))


def wait_and_set_default(node_name: str | None = None, timeout: float = 10.0) -> bool:
    """Wait for the K7 sink (or a named node) to appear, then make it the default.

    The K7's node name can change when WirePlumber restarts or the K7 is
    replugged (e.g. "...analog-stereo.2" -> "...analog-stereo"), and then
    WirePlumber's remembered default no longer matches and it falls back to
    another output. Setting it again by id fixes that.
    """
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = dump()
        if d:
            nid = (find_node_id(d, node_name) if node_name
                   else next((s["id"] for s in parse_sinks(d)), None))
            if nid is not None:
                return set_default(nid)
        time.sleep(0.3)
    return False
