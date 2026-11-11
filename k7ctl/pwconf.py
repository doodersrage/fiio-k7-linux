"""Shared helpers: config drop-ins for PipeWire/WirePlumber and live settings."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HEADER = "# Written by k7ctl (https://github.com/doodersrage/fiio-k7-linux).\n"


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))


def cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"))


def pipewire_dropin(name: str) -> Path:
    return config_home() / "pipewire" / "pipewire.conf.d" / name


def wireplumber_dropin(name: str) -> Path:
    return config_home() / "wireplumber" / "wireplumber.conf.d" / name


def write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + body)
    return path


def remove(path: Path) -> bool:
    if path.is_file():
        path.unlink()
        return True
    return False


# ------------------------------------------------------- live "settings" metadata

def parse_settings(pw_metadata_output: str) -> dict[str, str]:
    out = {}
    for line in pw_metadata_output.splitlines():
        m = re.search(r"key:'([^']*)' value:'([^']*)'", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def read_settings() -> dict[str, str] | None:
    if not shutil.which("pw-metadata"):
        return None
    try:
        out = subprocess.run(["pw-metadata", "-n", "settings"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_settings(out)


def set_setting(key: str, value: str) -> bool:
    if not shutil.which("pw-metadata"):
        return False
    try:
        r = subprocess.run(["pw-metadata", "-n", "settings", "0", key, value],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def systemctl_user(*args: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# ----------------------------------------------------- systemd user services

def unit_path(name: str) -> Path:
    return config_home() / "systemd" / "user" / name


def render_unit(description: str, k7ctl_args: str, undo: str) -> str:
    """A user service that runs `python -m k7ctl <args>` alongside PipeWire."""
    pkg_parent = Path(__file__).resolve().parent.parent
    return f"""\
# Written by k7ctl. Remove with: {undo}
[Unit]
Description={description}
After=pipewire.service wireplumber.service
BindsTo=pipewire.service

[Service]
Environment=PYTHONPATH={pkg_parent}
ExecStart={sys.executable} -m k7ctl {k7ctl_args}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=pipewire.service
"""


def install_service(name: str, unit: str) -> bool:
    """Write, enable and (re)start a user service. Returns whether it started."""
    path = unit_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit)
    systemctl_user("daemon-reload")
    systemctl_user("enable", name)
    return systemctl_user("restart", name)


def remove_service(name: str) -> None:
    systemctl_user("disable", "--now", name)
    path = unit_path(name)
    if path.is_file():
        path.unlink()
        systemctl_user("daemon-reload")
