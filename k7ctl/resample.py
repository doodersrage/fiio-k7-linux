"""Resampler quality, for the times PipeWire has to convert sample rates.

With native rates on, music usually reaches the K7 unconverted. PipeWire still
converts when two apps play at different rates, with the EQ/crossfeed on, or
for outputs that can't follow. Its default quality (4) is very clean (errors
~140 dB below the music) but starts cutting treble around 19 kHz: measured
44.1 -> 48 kHz, -1.4 dB at 19.5 kHz and -5.5 dB at 20.5 kHz. Quality 10 is
flat to 20.5 kHz, 14 to 21.5 kHz, for a little more CPU while converting.

Saved for PipeWire's own clients (client.conf.d) and for PulseAudio clients
(pipewire-pulse.conf.d: Firefox, Spotify, most desktop apps). Apps pick it up
when they start; PulseAudio clients only after pipewire-pulse restarts.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import pwconf

PRESETS = {
    "default": (4, "PipeWire's default: very clean, treble rolls off from about 19 kHz"),
    "high": (10, "flat to 20.5 kHz; a little more CPU while converting"),
    "max": (14, "flat to 21.5 kHz; most CPU while converting"),
}
CONF_NAME = "50-k7ctl-resample.conf"


def conf_paths() -> list[Path]:
    base = pwconf.config_home() / "pipewire"
    return [base / "client.conf.d" / CONF_NAME, base / "pipewire-pulse.conf.d" / CONF_NAME]


def render_conf(quality: int) -> str:
    return (
        f"# Resampler quality {quality} for streams. Remove with: k7ctl resample default\n"
        "stream.properties = {\n"
        f"    resample.quality = {quality}\n"
        "}\n"
    )


def quality() -> int:
    """The configured quality (PipeWire's default 4 if k7ctl hasn't set one)."""
    for p in conf_paths():
        try:
            m = re.search(r"resample\.quality\s*=\s*(\d+)", p.read_text())
        except OSError:
            continue
        if m:
            return int(m.group(1))
    return PRESETS["default"][0]


def current() -> str:
    q = quality()
    return next((name for name, (v, _) in PRESETS.items() if v == q), f"custom ({q})")


def restart_pulse() -> bool:
    return pwconf.systemctl_user("restart", "pipewire-pulse")


def apply(preset: str, restart: bool = False) -> bool:
    """Save a preset. Returns True if pipewire-pulse was restarted to apply it now."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    if preset == "default":
        for p in conf_paths():
            pwconf.remove(p)
    else:
        for p in conf_paths():
            pwconf.write(p, render_conf(PRESETS[preset][0]))
    return restart_pulse() if restart else False
