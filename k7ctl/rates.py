"""Let PipeWire run at the music's own sample rate instead of always 48 kHz.

By default PipeWire's clock only allows 48000 Hz, so a 44.1 kHz track (CD,
most streaming) or a hi-res file is resampled before it reaches the DAC.
Allowing more rates lets PipeWire switch the K7 to the rate of whatever is
playing, as long as nothing else is already playing at a different rate.

Two parts:
  * a drop-in file in ~/.config/pipewire/pipewire.conf.d/ (survives reboots)
  * the same setting applied live with pw-metadata (no restart, no dropout)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

K7_RATES = [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
DEFAULT_RATES = [48000]

CONF_NAME = "50-k7ctl-samplerates.conf"


def conf_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "pipewire" / "pipewire.conf.d"


def conf_path() -> Path:
    return conf_dir() / CONF_NAME


def render_conf(rates: list[int]) -> str:
    rate_list = " ".join(str(r) for r in rates)
    return (
        "# Written by k7ctl (https://github.com/doodersrage/fiio-k7-linux).\n"
        "# Lets PipeWire switch to the sample rate of what is playing so the\n"
        "# FiiO K7 gets it unresampled. Remove with: k7ctl rates disable\n"
        "context.properties = {\n"
        f"    default.clock.allowed-rates = [ {rate_list} ]\n"
        "}\n"
    )


def _format_rates(rates: list[int]) -> str:
    return "[ " + " ".join(str(r) for r in rates) + " ]"


def parse_allowed_rates(pw_metadata_output: str) -> list[int] | None:
    """Pull clock.allowed-rates out of `pw-metadata -n settings` output."""
    for line in pw_metadata_output.splitlines():
        if "'clock.allowed-rates'" in line:
            m = re.search(r"value:'\[([^\]]*)\]'", line)
            if m:
                return [int(x) for x in re.findall(r"\d+", m.group(1))]
    return None


def live_allowed_rates() -> list[int] | None:
    if not shutil.which("pw-metadata"):
        return None
    try:
        out = subprocess.run(["pw-metadata", "-n", "settings"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_allowed_rates(out)


def apply_live(rates: list[int]) -> bool:
    """Change the running PipeWire's allowed rates. Returns True on success."""
    if not shutil.which("pw-metadata"):
        return False
    try:
        r = subprocess.run(
            ["pw-metadata", "-n", "settings", "0", "clock.allowed-rates", _format_rates(rates)],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def is_enabled() -> bool:
    return conf_path().is_file()


def enable(apply: bool = True) -> tuple[Path, bool]:
    """Write the drop-in and (optionally) apply it live. Returns (path, applied_live)."""
    path = conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_conf(K7_RATES))
    applied = apply_live(K7_RATES) if apply else False
    return path, applied


def disable(apply: bool = True) -> tuple[bool, bool]:
    """Remove our drop-in. Returns (removed, applied_live).

    Only our own file is removed; the live setting goes back to 48 kHz only,
    PipeWire's default. If you had your own allowed-rates config elsewhere,
    restart PipeWire to pick it up again.
    """
    path = conf_path()
    removed = False
    if path.is_file():
        path.unlink()
        removed = True
    applied = apply_live(DEFAULT_RATES) if apply else False
    return removed, applied
