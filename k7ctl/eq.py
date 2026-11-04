"""Headphone EQ: a PipeWire filter-chain sink in front of the K7.

Profiles are parametric EQ files in the Equalizer APO format that AutoEQ
publishes for thousands of headphones ("Preamp: -6.3 dB", "Filter 1: ON PK Fc
105 Hz Gain 6.5 dB Q 0.70", ...). k7ctl parses the file itself and builds the
filter graph, including the preamp, so boosted bands can't clip.

How it runs: a systemd user service (k7ctl-eq.service) starts a small
PipeWire client that creates the "FiiO K7 + EQ" output. That output is made
the default and sends its audio to the K7. Turning EQ off stops the service
and makes the K7 the default again.

EQ means the audio is processed, so playback is no longer bit-perfect.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__, pipewire, pwconf

SERVICE = "k7ctl-eq.service"
NODE = pipewire.EQ_NODE

AUTOEQ_RAW = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results"
INDEX_URL = f"{AUTOEQ_RAW}/INDEX.md"
INDEX_MAX_AGE = 7 * 24 * 3600

# Equalizer APO filter type -> PipeWire biquad
TYPES = {
    "PK": "bq_peaking", "PEQ": "bq_peaking",
    "LS": "bq_lowshelf", "LSC": "bq_lowshelf", "LSQ": "bq_lowshelf",
    "HS": "bq_highshelf", "HSC": "bq_highshelf", "HSQ": "bq_highshelf",
    "LP": "bq_lowpass", "LPQ": "bq_lowpass",
    "HP": "bq_highpass", "HPQ": "bq_highpass",
    "NO": "bq_notch", "AP": "bq_allpass", "BP": "bq_bandpass",
}
DEFAULT_Q = 0.707


class EqError(Exception):
    pass


# ------------------------------------------------------------------ profiles

@dataclass
class Filter:
    type: str       # PipeWire biquad type
    freq: float
    gain: float
    q: float


@dataclass
class Profile:
    preamp: float | None
    filters: list[Filter]

    @property
    def safe_preamp(self) -> float:
        """The file's preamp, or (if it has none) enough cut to undo the biggest boost."""
        if self.preamp is not None:
            return self.preamp
        boosts = [f.gain for f in self.filters
                  if f.type in ("bq_peaking", "bq_lowshelf", "bq_highshelf") and f.gain > 0]
        return -max(boosts) if boosts else 0.0


FILTER_RE = re.compile(
    r"^Filter\s*\d*\s*:\s*(ON|OFF)\s+([A-Z]+)"
    r"(?:\s+[\d.]+\s*dB)?"                      # optional slope, e.g. "LS 12 dB"
    r"\s+Fc\s+([\d.]+)\s*Hz"
    r"(?:\s+Gain\s+(-?[\d.]+)\s*dB)?"
    r"(?:\s+Q\s+([\d.]+))?",
    re.IGNORECASE,
)
PREAMP_RE = re.compile(r"^Preamp\s*:\s*(-?[\d.]+)\s*dB", re.IGNORECASE)


def parse_profile(text: str) -> Profile:
    preamp = None
    filters = []
    for raw in text.splitlines():
        line = raw.strip()
        m = PREAMP_RE.match(line)
        if m:
            preamp = (preamp or 0.0) + float(m.group(1))
            continue
        m = FILTER_RE.match(line)
        if not m:
            continue
        state, kind, fc, gain, q = m.groups()
        if state.upper() != "ON":
            continue
        kind = kind.upper()
        if kind not in TYPES:
            raise EqError(f"unsupported filter type {kind!r} in: {line}")
        filters.append(Filter(TYPES[kind], float(fc), float(gain or 0.0),
                              float(q) if q else DEFAULT_Q))
    if not filters and preamp is None:
        raise EqError("no 'Preamp:' or 'Filter N: ON ...' lines found; "
                      "expected an Equalizer APO / AutoEQ ParametricEQ.txt file")
    return Profile(preamp, filters)


def profiles_dir() -> Path:
    return pwconf.data_home() / "k7ctl" / "eq"


def state_path() -> Path:
    return pwconf.config_home() / "k7ctl" / "eq.json"


def safe_name(name: str) -> str:
    name = re.sub(r"[/\\\x00]", "-", name).strip().strip(".")
    if not name:
        raise EqError("empty profile name")
    return name


def list_profiles() -> list[str]:
    d = profiles_dir()
    if not d.is_dir():
        return []
    return sorted((p.stem for p in d.glob("*.txt")), key=str.lower)


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{safe_name(name)}.txt"


def save_profile(name: str, text: str) -> Path:
    parse_profile(text)  # refuse anything we can't use
    path = profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def import_file(src: str, name: str | None = None) -> tuple[str, Path]:
    text = Path(src).read_text(errors="replace")
    name = name or re.sub(r"\s*ParametricEQ$", "", Path(src).stem)
    return name, save_profile(name, text)


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if path.is_file():
        path.unlink()
        return True
    return False


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> None:
    s = load_state()
    s.update(kw)
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2) + "\n")


def selected_profile() -> str | None:
    name = load_state().get("profile")
    if name and profile_path(name).is_file():
        return name
    profiles = list_profiles()
    return profiles[0] if len(profiles) == 1 else None


# ------------------------------------------------------------------- AutoEQ

@dataclass
class AutoEqEntry:
    name: str
    path: str        # URL-encoded path under results/, e.g. oratory1990/over-ear/Sennheiser%20HD%20600
    source: str      # e.g. "oratory1990" or "crinacle on GRAS 43AG-7"

    @property
    def url(self) -> str:
        leaf = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
        return f"{AUTOEQ_RAW}/{self.path}/{urllib.parse.quote(leaf + ' ParametricEQ.txt')}"


INDEX_LINE = re.compile(r"^- \[(.+?)\]\(\./(.+)\) by (.+)$")


def parse_index(text: str) -> list[AutoEqEntry]:
    out = []
    for line in text.splitlines():
        m = INDEX_LINE.match(line.strip())
        if m:
            out.append(AutoEqEntry(m.group(1), m.group(2), m.group(3).strip()))
    return out


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": f"k7ctl/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except OSError as e:
        raise EqError(f"couldn't download {url}: {e}") from e


def autoeq_index(refresh: bool = False) -> list[AutoEqEntry]:
    cache = pwconf.cache_home() / "k7ctl" / "autoeq-index.md"
    fresh = cache.is_file() and time.time() - cache.stat().st_mtime < INDEX_MAX_AGE
    if refresh or not fresh:
        try:
            text = _fetch(INDEX_URL)
        except EqError:
            if cache.is_file():
                return parse_index(cache.read_text())
            raise
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    return parse_index(cache.read_text())


def search(query: str, entries: list[AutoEqEntry] | None = None) -> list[AutoEqEntry]:
    entries = autoeq_index() if entries is None else entries
    words = query.lower().split()
    hits = [e for e in entries if all(w in e.name.lower() for w in words)]
    q = query.strip().lower()
    # exact name matches first, then by name; keep AutoEQ's source order within a name
    return sorted(hits, key=lambda e: (e.name.lower() != q, e.name.lower()))


def download(entry: AutoEqEntry, name: str | None = None) -> tuple[str, Path]:
    text = _fetch(entry.url)
    name = name or entry.name
    return name, save_profile(name, text)


# ------------------------------------------------------ PipeWire config + service

BASE_CONF = """\
context.properties = { log.level = 0 }
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}
"""


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_conf(profile: Profile, profile_name: str, target: str) -> str:
    filters = [f"{{ type = bq_highshelf freq = 0 gain = {profile.safe_preamp:.2f} q = 1.0 }}"]
    filters += [f"{{ type = {f.type} freq = {f.freq:g} gain = {f.gain:.2f} q = {f.q:g} }}"
                for f in profile.filters]
    filter_lines = "\n".join(" " * 24 + f for f in filters)
    return f"""{BASE_CONF}context.modules = [
    {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = {_q("FiiO K7 + EQ (" + profile_name + ")")}
            media.name = {_q("k7ctl EQ")}
            audio.channels = 2
            audio.position = [ FL FR ]
            filter.graph = {{
                nodes = [
                    {{ type = builtin name = eq label = param_eq
                      config = {{
                        filters = [
{filter_lines}
                        ]
                      }}
                    }}
                ]
            }}
            capture.props = {{
                node.name = {_q(NODE)}
                media.class = Audio/Sink
                device.icon-name = "audio-card"
            }}
            playback.props = {{
                node.name = {_q(NODE + ".output")}
                node.passive = true
                node.dont-fallback = true
                stream.dont-remix = true
                target.object = {_q(target)}
            }}
        }}
    }}
]
"""


def runtime_conf_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or str(pwconf.cache_home())
    return Path(base) / "k7ctl" / "eq.conf"


def unit_path() -> Path:
    return pwconf.config_home() / "systemd" / "user" / SERVICE


def render_unit() -> str:
    pkg_parent = Path(__file__).resolve().parent.parent
    return f"""\
# Written by k7ctl. Remove with: k7ctl eq off
[Unit]
Description=k7ctl headphone EQ for the FiiO K7
After=pipewire.service wireplumber.service
BindsTo=pipewire.service

[Service]
Environment=PYTHONPATH={pkg_parent}
ExecStart={sys.executable} -m k7ctl eq _run
Restart=on-failure
RestartSec=3

[Install]
WantedBy=pipewire.service
"""


def _k7_sink_name() -> str | None:
    d = pipewire.dump()
    sinks = pipewire.parse_sinks(d) if d else []
    return sinks[0]["name"] if sinks else None


def run_daemon(poll: float = 3.0) -> int:
    """Entry point for the service.

    Waits for the K7, then runs a PipeWire client with the EQ filter pointed at
    it. The K7's node name can change (WirePlumber restart, replugging), so it
    keeps checking and restarts the filter with the new name when it does.
    """
    import signal
    import subprocess

    name = selected_profile()
    if not name:
        print("k7ctl eq: no profile selected", file=sys.stderr)
        return 1
    profile = parse_profile(profile_path(name).read_text(errors="replace"))
    pw = shutil.which("pipewire") or "/usr/bin/pipewire"
    conf = runtime_conf_path()
    conf.parent.mkdir(parents=True, exist_ok=True)

    child: subprocess.Popen | None = None
    target: str | None = None
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            current = _k7_sink_name()
            if current and current != target:
                if child:
                    child.terminate()
                    child.wait(timeout=5)
                conf.write_text(render_conf(profile, name, current))
                print(f"k7ctl eq: {name} -> {current}", flush=True)
                child = subprocess.Popen([pw, "-c", str(conf)])
                target = current
                was_default = load_state().get("default", True)
                if was_default:
                    pipewire.wait_and_set_default(NODE, timeout=5)
            if child and child.poll() is not None:
                print(f"k7ctl eq: pipewire exited ({child.returncode})", file=sys.stderr)
                return 1
            for _ in range(int(poll * 10)):
                if stopping:
                    break
                time.sleep(0.1)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
    return 0


def is_running() -> bool:
    return pwconf.systemctl_user("is-active", "--quiet", SERVICE)


def _wait_for_node(timeout: float = 8.0) -> int | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = pipewire.dump()
        nid = pipewire.find_node_id(d, NODE) if d else None
        if nid is not None:
            return nid
        time.sleep(0.25)
    return None


def enable(name: str | None = None, make_default: bool = True) -> str:
    """Turn EQ on with a profile (or the selected one). Returns the profile name."""
    if not shutil.which("systemctl"):
        raise EqError("the EQ runs as a systemd user service, and systemctl wasn't found")
    if name is not None:
        if not profile_path(name).is_file():
            raise EqError(f"no profile called {name!r}; see: k7ctl eq list")
        save_state(profile=name)
    name = selected_profile()
    if not name:
        raise EqError("pick a profile first: k7ctl eq on \"<name>\" "
                      "(see k7ctl eq list, or get one with k7ctl eq search)")
    parse_profile(profile_path(name).read_text(errors="replace"))
    save_state(profile=name, enabled=True)

    unit = unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_unit())
    pwconf.systemctl_user("daemon-reload")
    pwconf.systemctl_user("enable", SERVICE)
    if not pwconf.systemctl_user("restart", SERVICE):
        raise EqError(f"couldn't start {SERVICE}; see: journalctl --user -u {SERVICE}")
    save_state(default=make_default)
    nid = _wait_for_node()
    if nid is None:
        raise EqError("the EQ started but its output didn't appear (is the K7 connected?); "
                      f"see: journalctl --user -u {SERVICE}")
    if make_default:
        pipewire.set_default(nid)
    return name


def disable() -> None:
    d = pipewire.dump()
    eq_was_default = bool(d) and pipewire.parse_default_sink(d) == NODE
    save_state(enabled=False)
    pwconf.systemctl_user("disable", "--now", SERVICE)
    unit = unit_path()
    if unit.is_file():
        unit.unlink()
        pwconf.systemctl_user("daemon-reload")
    if eq_was_default:
        pipewire.wait_and_set_default(None, timeout=5)
