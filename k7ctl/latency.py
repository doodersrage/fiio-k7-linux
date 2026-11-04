"""Buffer size (PipeWire "quantum") presets.

At 352.8/384 kHz each buffer lasts a quarter as long as at 96 kHz, so a busy
machine is more likely to miss one and you hear crackles. A bigger buffer
fixes that at the cost of latency (fine for music, noticeable in games and
video calls). The low-latency preset goes the other way.

Values are in samples (frames). Applied live through PipeWire's settings
metadata and saved as a drop-in so they survive a restart.
"""

from __future__ import annotations

from pathlib import Path

from . import pwconf

CONF_NAME = "51-k7ctl-latency.conf"

# name: (quantum, min-quantum, max-quantum), description
PRESETS: dict[str, tuple[tuple[int, int, int], str]] = {
    "default": ((1024, 32, 2048), "PipeWire's defaults"),
    "stable": ((2048, 1024, 8192),
               "bigger buffers: fixes crackles at high sample rates, more latency"),
    "low-latency": ((256, 64, 512),
                    "small buffers for games and calls; may crackle at high rates"),
}
KEYS = ("clock.quantum", "clock.min-quantum", "clock.max-quantum")


def conf_path() -> Path:
    return pwconf.pipewire_dropin(CONF_NAME)


def render_conf(preset: str) -> str:
    q, lo, hi = PRESETS[preset][0]
    return (
        f"# Latency preset '{preset}'. Remove with: k7ctl latency default\n"
        "context.properties = {\n"
        f"    default.clock.quantum = {q}\n"
        f"    default.clock.min-quantum = {lo}\n"
        f"    default.clock.max-quantum = {hi}\n"
        "}\n"
    )


def current(settings: dict[str, str] | None = None) -> str | None:
    """Which preset the running PipeWire matches, 'custom', or None if unknown."""
    s = pwconf.read_settings() if settings is None else settings
    if not s or not all(k in s for k in KEYS):
        return None
    try:
        values = tuple(int(s[k]) for k in KEYS)
    except ValueError:
        return None
    for name, (v, _) in PRESETS.items():
        if v == values:
            return name
    return "custom"


def apply(preset: str, live: bool = True) -> bool:
    """Save and apply a preset. Returns True if applied to the running PipeWire."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    if preset == "default":
        pwconf.remove(conf_path())
    else:
        pwconf.write(conf_path(), render_conf(preset))
    if not live:
        return False
    # Order matters: PipeWire clamps quantum to [min, max], so widen the
    # range first, then set the quantum inside it.
    q, lo, hi = PRESETS[preset][0]
    ok = pwconf.set_setting("clock.min-quantum", "1")
    ok &= pwconf.set_setting("clock.max-quantum", str(hi))
    ok &= pwconf.set_setting("clock.min-quantum", str(lo))
    ok &= pwconf.set_setting("clock.quantum", str(q))
    return ok
