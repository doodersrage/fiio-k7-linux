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
import math
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__, device, pipewire, pwconf

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


def next_profile() -> str:
    """The saved profile after the selected one (wrapping around)."""
    profiles = list_profiles()
    if not profiles:
        raise EqError('no saved profiles; get one with: k7ctl eq search "<headphone name>"')
    sel = selected_profile()
    if sel not in profiles:
        return profiles[0]
    return profiles[(profiles.index(sel) + 1) % len(profiles)]


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


# Crossfeed presets (Bauer stereophonic-to-binaural, the classic bs2b settings):
# name: (cutoff Hz, how far below the direct sound the crossfed low frequencies are, in dB)
CROSSFEED = {
    "light": (650, 9.5),    # Jan Meier
    "medium": (700, 6.0),   # Chu Moy
    "strong": (700, 4.5),   # bs2b default
}
# Every rate the graph may run at; bq_raw uses the set matching the current rate.
CROSSFEED_RATES = (44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000)


def crossfeed_gains(feed_db: float) -> tuple[float, float, float]:
    """(direct gain, cross gain, treble shelf dB) at low frequencies; direct + cross = 1."""
    ratio = 10 ** (-feed_db / 20)
    direct = 1 / (1 + ratio)
    cross = ratio / (1 + ratio)
    return direct, cross, -20 * math.log10(direct)


def crossfeed_coefficients(fc: float, feed_db: float, rate: float) -> tuple[tuple, tuple]:
    """First-order direct-path shelf and cross-path low-pass, as biquad coefficients.

    Analog prototypes (w = 2*pi*fc): direct (s + d*w)/(s + w), cross c*w/(s + w).
    With d + c = 1 they sum to exactly 1, so sound that's the same in both
    channels (centred) is unchanged at every frequency. The low-pass also
    delays the crossfed bass by 1/w (~0.23 ms at 700 Hz): the time difference
    between the ears, without a separate delay that would make centred sound
    dip. Bilinear transform with prewarping at fc.
    """
    d, c, _ = crossfeed_gains(feed_db)
    K = math.tan(math.pi * fc / rate)
    a0, a1 = 1 + K, K - 1
    direct = ((1 + d * K) / a0, (d * K - 1) / a0, 0.0, 1.0, a1 / a0, 0.0)
    cross = (c * K / a0, c * K / a0, 0.0, 1.0, a1 / a0, 0.0)
    return direct, cross


def _bq_raw(name: str, which: int, fc: float, feed: float) -> str:
    sets = []
    for rate in CROSSFEED_RATES:
        b0, b1, b2, a0, a1, a2 = crossfeed_coefficients(fc, feed, rate)[which]
        sets.append(f"{{ rate = {rate} b0 = {b0:.12g} b1 = {b1:.12g} b2 = {b2:.12g} "
                    f"a0 = {a0:.12g} a1 = {a1:.12g} a2 = {a2:.12g} }}")
    return (f"{{ type = builtin name = {name} label = bq_raw config = {{ coefficients = [ "
            + " ".join(sets) + " ] } }")


def _crossfeed_nodes(preset: str, ind: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Builtin nodes for crossfeed. Returns (nodes, links, inputs, outputs)."""
    fc, feed = CROSSFEED[preset]
    nodes, links = [], []
    for ch in ("L", "R"):
        nodes += [
            f"{{ type = builtin name = cf_in{ch} label = copy }}",
            _bq_raw(f"cf_direct{ch}", 0, fc, feed),
            _bq_raw(f"cf_cross{ch}", 1, fc, feed),
            f"{{ type = builtin name = cf_mix{ch} label = mixer "
            f"control = {{ \"Gain 1\" = 1.0 \"Gain 2\" = 1.0 }} }}",
        ]
    for ch, other in (("L", "R"), ("R", "L")):
        links += [
            f'{{ output = "cf_in{ch}:Out" input = "cf_direct{ch}:In" }}',
            f'{{ output = "cf_in{other}:Out" input = "cf_cross{ch}:In" }}',
            f'{{ output = "cf_direct{ch}:Out" input = "cf_mix{ch}:In 1" }}',
            f'{{ output = "cf_cross{ch}:Out" input = "cf_mix{ch}:In 2" }}',
        ]
    return ([ind + n for n in nodes], [ind + l for l in links],
            ['"cf_inL:In"', '"cf_inR:In"'], ['"cf_mixL:Out"', '"cf_mixR:Out"'])


def chain_description(device_name: str, profile_name: str | None, crossfeed: str | None) -> str:
    parts = []
    if profile_name:
        parts.append(f"EQ ({profile_name})")
    if crossfeed:
        parts.append("crossfeed")
    return device_name + " + " + " + ".join(parts)


def render_conf(profile: Profile | None, profile_name: str | None, target: str,
                device_name: str = "FiiO K7", crossfeed: str | None = None,
                quality: int | None = None) -> str:
    """The filter-chain config: EQ, crossfeed, or EQ then crossfeed."""
    if profile is None and not crossfeed:
        raise EqError("nothing to do: no EQ profile and no crossfeed")
    ind = " " * 20
    nodes, links, inputs, outputs = [], [], [], []
    if profile is not None:
        filters = [f"{{ type = bq_highshelf freq = 0 gain = {profile.safe_preamp:.2f} q = 1.0 }}"]
        filters += [f"{{ type = {f.type} freq = {f.freq:g} gain = {f.gain:.2f} q = {f.q:g} }}"
                    for f in profile.filters]
        filter_lines = "\n".join(" " * 28 + f for f in filters)
        nodes.append(f"""{ind}{{ type = builtin name = eq label = param_eq
{ind}  config = {{
{ind}    filters = [
{filter_lines}
{ind}    ]
{ind}  }}
{ind}}}""")
        inputs, outputs = ['"eq:In 1"', '"eq:In 2"'], ['"eq:Out 1"', '"eq:Out 2"']
    if crossfeed:
        if crossfeed not in CROSSFEED:
            raise EqError(f"unknown crossfeed preset {crossfeed!r}")
        cf_nodes, cf_links, cf_in, cf_out = _crossfeed_nodes(crossfeed, ind)
        nodes += cf_nodes
        links += cf_links
        if outputs:     # EQ first, then crossfeed
            links += [f'{ind}{{ output = {o} input = {i} }}' for o, i in zip(outputs, cf_in)]
        else:
            inputs = cf_in
        outputs = cf_out
    q = f"\n                resample.quality = {quality}" if quality else ""
    nl = "\n"
    return f"""{BASE_CONF}context.modules = [
    {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = {_q(chain_description(device_name, profile_name if profile else None, crossfeed))}
            media.name = {_q("k7ctl EQ")}
            audio.channels = 2
            audio.position = [ FL FR ]
            filter.graph = {{
                nodes = [
{nl.join(nodes)}
                ]
                links = [
{nl.join(links)}
                ]
                inputs = [ {" ".join(inputs)} ]
                outputs = [ {" ".join(outputs)} ]
            }}
            capture.props = {{
                node.name = {_q(NODE)}
                media.class = Audio/Sink
                device.icon-name = "audio-card"{q}
            }}
            playback.props = {{
                node.name = {_q(NODE + ".output")}
                node.passive = true
                node.dont-fallback = true
                stream.dont-remix = true
                target.object = {_q(target)}{q}
            }}
        }}
    }}
]
"""


def runtime_conf_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or str(pwconf.cache_home())
    return Path(base) / "k7ctl" / "eq.conf"


def unit_path() -> Path:
    return pwconf.unit_path(SERVICE)


def render_unit() -> str:
    return pwconf.render_unit("k7ctl headphone EQ", "eq _run", "k7ctl eq off")


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

    from . import resample
    state = load_state()
    name = selected_profile() if state.get("enabled") else None
    profile = parse_profile(profile_path(name).read_text(errors="replace")) if name else None
    crossfeed = crossfeed_preset()
    if profile is None and not crossfeed:
        print("k7ctl eq: neither EQ nor crossfeed is on", file=sys.stderr)
        return 0
    quality = resample.quality()
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
                conf.write_text(render_conf(profile, name, current, device.display_name(),
                                            crossfeed=crossfeed,
                                            quality=quality if quality != 4 else None))
                print(f"k7ctl eq: {chain_description('', name, crossfeed).lstrip(' +')} -> {current}",
                      flush=True)
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


def crossfeed_preset() -> str | None:
    cf = load_state().get("crossfeed")
    return cf if cf in CROSSFEED else None


def _start(make_default: bool = True) -> None:
    """(Re)start the processing service with the current state."""
    if not shutil.which("systemctl"):
        raise EqError("the EQ runs as a systemd user service, and systemctl wasn't found")
    if not pwconf.install_service(SERVICE, render_unit()):
        raise EqError(f"couldn't start {SERVICE}; see: journalctl --user -u {SERVICE}")
    save_state(default=make_default)
    nid = _wait_for_node()
    if nid is None:
        raise EqError("the EQ started but its output didn't appear (is the K7 connected?); "
                      f"see: journalctl --user -u {SERVICE}")
    if make_default:
        pipewire.set_default(nid)


def _stop() -> None:
    d = pipewire.dump()
    eq_was_default = bool(d) and pipewire.parse_default_sink(d) == NODE
    pwconf.remove_service(SERVICE)
    if eq_was_default:
        pipewire.wait_and_set_default(None, timeout=5)


def _apply(make_default: bool = True) -> None:
    state = load_state()
    if (state.get("enabled") and selected_profile()) or crossfeed_preset():
        _start(make_default)
    else:
        _stop()


def enable(name: str | None = None, make_default: bool = True) -> str:
    """Turn EQ on with a profile (or the selected one). Returns the profile name."""
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
    _start(make_default)
    return name


def disable() -> None:
    """Turn the EQ off (crossfeed stays on if it is)."""
    save_state(enabled=False)
    _apply()


def set_crossfeed(preset: str | None) -> str | None:
    """Turn crossfeed on with a preset, or off with None/"off". Returns the preset."""
    if preset in (None, "off"):
        save_state(crossfeed="off")
    elif preset in CROSSFEED:
        save_state(crossfeed=preset)
    else:
        raise EqError(f"unknown crossfeed preset {preset!r} (light, medium, strong or off)")
    _apply()
    return crossfeed_preset()


def restart_if_running() -> bool:
    """Re-read settings (e.g. resampler quality) if the service is running."""
    if is_running():
        _start(make_default=load_state().get("default", True))
        return True
    return False
