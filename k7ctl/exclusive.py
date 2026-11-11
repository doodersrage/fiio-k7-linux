"""Hand the DAC to a player that uses it directly (e.g. MPD for native DSD).

PipeWire can't send DSD to the K7, so DSD needs a player that opens the K7's
ALSA device (hw:CARD=K7) itself. That fails with "Device or resource busy"
while PipeWire has the device open (always, with keep-awake on).

"Exclusive on" switches the DAC's PipeWire profile to "off": PipeWire closes the
device and its output disappears, so apps use another output meanwhile.
"Exclusive off" restores the previous profile and makes the DAC the default
output again if it was before. WirePlumber remembers profiles, so the DAC
stays released (also after replugging or a reboot) until exclusive is off.

Automatic mode: a user service (k7ctl-exclusive.service) watches for the
players you name, turns exclusive on when one starts and off again when the
last one closes. It only undoes what it did itself: if you turn exclusive on
by hand it stays on, and if you turn it off while a player runs it stays off.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from . import device, pipewire, pwconf, settings

OFF = "off"
SERVICE = "k7ctl-exclusive.service"


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


def enable(auto: bool = False) -> str:
    """Release the DAC. Returns a message; raises RuntimeError if it can't.

    auto: the watcher did it, so the watcher may undo it."""
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
    state = {"profile": dev["profile"], "was_default": pipewire.k7_is_default(d)}
    if auto:
        state["auto"] = True
    _save(**state)
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



def started_automatically() -> bool:
    """Whether exclusive mode is on because the watcher turned it on."""
    return bool(_load().get("auto"))


# ------------------------------------------------------------ automatic mode

def running_players(names, proc: Path = Path("/proc")) -> set[str]:
    """Which of the named programs are running (names as in ps/top, any case)."""
    want = {n.strip().lower() for n in names if n.strip()}
    found: set[str] = set()
    if not want:
        return found
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return found
    for p in pids:
        try:
            comm = (p / "comm").read_text().strip().lower()
        except OSError:
            continue
        names_of = {comm}
        try:
            argv0 = (p / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
        except OSError:
            argv0 = ""
        if argv0:
            names_of.add(os.path.basename(argv0).lower())
        found |= names_of & want
        # the kernel cuts comm to 15 characters
        found |= {w for w in want if len(w) > 15 and comm == w[:15]}
    return found


class Watcher:
    """Turns exclusive mode on while a named player runs. Call tick() regularly."""

    def __init__(self, find=running_players):
        self.find = find
        self.pending = True     # the next time a player is seen, release the DAC

    def tick(self, players) -> str | None:
        """Check once. Returns a message when it changed something."""
        running = self.find(players)
        if running:
            if not self.pending:
                return None
            if is_on():
                self.pending = False    # already released (by hand): not ours to undo
                return None
            try:
                enable(auto=True)
            except RuntimeError:
                return None             # e.g. the DAC is unplugged: try again next time
            self.pending = False
            return f"{', '.join(sorted(running))} started: released the {device.display_name()}"
        self.pending = True
        if started_automatically():
            try:
                if is_on():
                    disable()
                    return f"No player running: PipeWire uses the {device.display_name()} again"
                _save()   # turned off by hand meanwhile
            except RuntimeError:
                return None
        return None


def run_watcher(poll: float = 2.0) -> int:
    """Entry point for the service."""
    import signal
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    w = Watcher()
    while not stopping:
        players = auto_players()
        if not players:
            print("k7ctl exclusive: no players to watch", file=sys.stderr)
            return 0
        msg = w.tick(players)
        if msg:
            print(f"k7ctl exclusive: {msg}", flush=True)
        for _ in range(int(poll * 10)):
            if stopping:
                break
            time.sleep(0.1)
    return 0


def auto_players() -> list[str]:
    players = settings.get("auto_exclusive")
    return [str(p) for p in players] if isinstance(players, list) else []


def parse_players(text: str) -> list[str]:
    """'strawberry, mpd  audacious' -> ['strawberry', 'mpd', 'audacious']."""
    out: list[str] = []
    for n in re.split(r"[,\s]+", text):
        if n and n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out


def unit_path() -> Path:
    return pwconf.unit_path(SERVICE)


def render_unit() -> str:
    return pwconf.render_unit("k7ctl automatic exclusive mode", "exclusive _watch",
                              "k7ctl exclusive auto off")


def watcher_running() -> bool:
    return pwconf.systemctl_user("is-active", "--quiet", SERVICE)


def set_auto_players(players: list[str]) -> list[str]:
    """Watch these players (an empty list turns automatic mode off)."""
    settings.set(auto_exclusive=list(players))
    if players:
        if not pwconf.install_service(SERVICE, render_unit()):
            raise RuntimeError(f"couldn't start {SERVICE}; see: journalctl --user -u {SERVICE}")
    else:
        pwconf.remove_service(SERVICE)
        if started_automatically() and is_on():
            disable()
    return list(players)
