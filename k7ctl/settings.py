"""k7ctl's own settings: ~/.config/k7ctl/settings.json."""

from __future__ import annotations

import json
from pathlib import Path

from . import pwconf

DEFAULTS = {
    "device": "auto",        # "auto" (the FiiO K7) or a USB id "vvvv:pppp"
    "notify_rate": False,    # tray: notify when the sample rate changes
    "notify_resample": False,  # tray: notify when something starts being resampled
}


def path() -> Path:
    return pwconf.config_home() / "k7ctl" / "settings.json"


def load() -> dict:
    s = dict(DEFAULTS)
    try:
        data = json.loads(path().read_text())
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def get(key: str):
    return load()[key]


def set(**values) -> dict:  # noqa: A001 - module-level API, used as settings.set(...)
    unknown = [k for k in values if k not in DEFAULTS]
    if unknown:
        raise KeyError(f"unknown setting(s): {', '.join(unknown)}")
    s = load()
    s.update(values)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2) + "\n")
    return s
