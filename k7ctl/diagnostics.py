"""A plain-text report for bug reports: `k7ctl diag`, or "Copy diagnostics" in the window.

Contains versions, the device's kernel status, PipeWire settings, k7ctl's own
config files and the EQ service's recent log. No personal files are read; the
only identifying details are application names that are playing audio.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, device, eq, keepawake, latency, rates, settings, snapshot


def _run(args: list[str], limit: int = 4000) -> str:
    if not shutil.which(args[0]):
        return f"({args[0]} not installed)"
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"({e})"
    out = (r.stdout + r.stderr).strip()
    return out[-limit:] if len(out) > limit else out


def _file(path: Path, limit: int = 3000) -> str:
    try:
        text = path.read_text(errors="replace").strip()
    except OSError:
        return "(not present)"
    return text[:limit]


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.rstrip()}\n"


def _qt_binding() -> str:
    try:
        from .qt import BINDING
        return BINDING
    except ImportError:
        return "none"


def report() -> str:
    parts = []
    parts.append(_section("k7ctl", "\n".join([
        f"k7ctl {__version__}, Python {platform.python_version()}, Qt binding: {_qt_binding()}",
        f"Kernel: {platform.release()}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '?')} ({os.environ.get('XDG_SESSION_TYPE', '?')})",
        f"PipeWire: {_run(['pipewire', '--version']).splitlines()[-1] if shutil.which('pipewire') else 'not found'}",
        f"WirePlumber: {_run(['wireplumber', '--version']).splitlines()[-1] if shutil.which('wireplumber') else 'not found'}",
        f"Device setting: {device.configured_id()} (looking for {device.target_id()})",
    ])))

    cards = device.list_usb_cards()
    parts.append(_section("USB sound cards", "\n".join(
        f"card {c.card_index} ({c.card_id}): {c.usb_id} {c.name}" for c in cards) or "(none)"))

    k7 = device.find()
    if k7:
        card = Path(f"/proc/asound/card{k7.card_index}")
        info = [f"{k7.name} {k7.usb_id}, firmware {k7.firmware}, {k7.usb_speed}, {k7.alsa_device}",
                f"stream: {k7.stream.describe()} ({k7.stream.state}, {k7.stream.format}, "
                f"{k7.stream.rate} Hz, measured {k7.stream.momentary_rate})"]
        parts.append(_section("Device", "\n".join(info)))
        parts.append(_section("stream0", _file(card / "stream0")))
        parts.append(_section("hw_params", _file(card / "pcm0p" / "sub0" / "hw_params")))
    else:
        parts.append(_section("Device", "not connected"))

    s = snapshot.take()
    lines = []
    if s.sink:
        sk = s.sink
        lines.append(f"sink {sk.id} {sk.name}: rate {sk.rate}, running {sk.running}, "
                     f"volume {sk.volume}, muted {sk.muted}, default {sk.is_default}, "
                     f"EQ default {sk.eq_is_default}")
        for x in sk.streams:
            lines.append(f"  stream {x.id} {x.app!r}: {x.rate} Hz {x.format}, "
                         f"{'running' if x.running else 'idle'}{', EQ' if x.is_eq else ''}")
    else:
        lines.append("no sink for the device")
    lines.append(f"bit-perfect: {s.bit_perfect}; resampled: {[x.app for x in s.resampled]}")
    lines.append(f"latency: {s.latency} (quantum {s.quantum}); keepawake live: {s.keepawake}")
    parts.append(_section("PipeWire", "\n".join(lines)))
    parts.append(_section("PipeWire settings", _run(["pw-metadata", "-n", "settings"])))
    parts.append(_section("Default nodes", "\n".join(
        l for l in _run(["pw-metadata", "0"]).splitlines() if "default" in l)))

    parts.append(_section("k7ctl settings", _file(settings.path())))
    for p in (rates.conf_path(), latency.conf_path(), keepawake.conf_path(),
              eq.state_path(), eq.unit_path()):
        parts.append(_section(str(p).replace(str(Path.home()), "~"), _file(p)))
    profiles = eq.list_profiles()
    parts.append(_section("EQ profiles", ", ".join(profiles) or "(none)"))
    parts.append(_section("EQ service", "\n".join([
        "active" if eq.is_running() else "inactive",
        _run(["journalctl", "--user", "-u", eq.SERVICE, "-n", "20", "--no-pager", "-o", "short-iso"],
             limit=3000),
    ])))
    text = "\n".join(parts)
    return text.replace(str(Path.home()), "~")


def main() -> int:
    sys.stdout.write(report())
    return 0

