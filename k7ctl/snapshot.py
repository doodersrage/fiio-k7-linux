"""One read of everything: the device, PipeWire, and k7ctl's settings.

Shared by the command line, status bar output and the tray icon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import device, eq, exclusive, keepawake, latency, pipewire, pwconf, resample
from .device import format_rate


@dataclass
class Snapshot:
    k7: device.K7 | None
    sink: pipewire.Sink | None
    allowed_rates: list[int] | None
    latency: str | None
    quantum: int | None
    keepawake: bool | None        # live state on the node; None if unknown
    keepawake_config: bool
    eq_profile: str | None
    eq_on: bool
    crossfeed: str | None = None  # crossfeed preset, while it's running
    resample: str | None = None   # resampler quality preset
    exclusive: bool = False       # PipeWire released the DAC (profile "off")
    direct: str | None = None     # a non-PipeWire program using the DAC directly

    # -------------------------------------------------------------- verdicts
    @property
    def connected(self) -> bool:
        return self.k7 is not None

    @property
    def playing(self) -> bool:
        return bool(self.k7 and self.k7.stream.state == "running")

    @property
    def resampled(self) -> list[pipewire.Stream]:
        return self.sink.resampled() if self.sink else []

    @property
    def processing(self) -> bool:
        """EQ and/or crossfeed is changing the sound."""
        return self.eq_on or bool(self.crossfeed)

    @property
    def single_rate(self) -> bool:
        return self.allowed_rates is not None and len(self.allowed_rates) <= 1

    @property
    def bit_perfect(self) -> bool | None:
        """True/False while playing; None when idle or unknown."""
        if self.playing and self.direct:
            return True               # a player has the DAC to itself: nothing in between
        if not self.playing or not self.sink:
            return None
        if self.processing or self.resampled:
            return False
        if self.sink.volume is not None and (self.sink.volume < 0.999 or self.sink.muted):
            return False
        return True

    def why_resampled(self, tr=lambda text: text) -> list[str]:
        """Why streams are resampled. The window and tray pass their translator;
        the command line stays English."""
        out = []
        if not self.sink or not self.sink.rate:
            return out
        holders = [s.app for s in self.sink.holders() if not s.is_eq]
        for s in self.resampled:
            args = dict(app=s.app, rate=format_rate(s.rate), dac_rate=format_rate(self.sink.rate),
                        others=", ".join(sorted(set(holders))))
            if self.single_rate:
                line = tr("{app} is {rate} but the K7 is running at {dac_rate} "
                          "(PipeWire only allows one rate: k7ctl rates enable)")
            elif holders:
                line = tr("{app} is {rate} but the K7 is running at {dac_rate} because {others} started first")
            else:
                line = tr("{app} is {rate} but the K7 is running at {dac_rate}")
            out.append(line.format(**args))
        return out

    # --------------------------------------------------------------- display
    def short(self) -> str:
        """Compact text for status bars."""
        if not self.k7:
            return ""
        s = self.k7.stream
        if s.state == "closed" or not s.rate:
            text = "K7"
        elif s.is_dsd:
            text = s.describe().split()[0]
        else:
            text = f"{s.rate / 1000:g}k"
        if self.resampled:
            text += "*"
        if self.eq_on:
            text += " EQ"
        if self.crossfeed:
            text += " XF"
        if self.exclusive and not self.playing:
            text += " (released)"
        return text

    def css_class(self) -> str:
        if not self.k7:
            return "disconnected"
        if self.exclusive or self.direct:
            return "playing" if self.playing else "idle"
        if self.processing:
            return "eq"
        if self.resampled:
            return "resampled"
        return "playing" if self.playing else "idle"


def take(with_volume: bool = True) -> Snapshot:
    k7 = device.find()
    d = pipewire.dump() if k7 else None
    sink = pipewire.sink_from_dump(d, with_volume) if d else None
    settings = pwconf.read_settings()
    allowed = None
    quantum = None
    if settings:
        if "clock.allowed-rates" in settings:
            allowed = [int(x) for x in re.findall(r"\d+", settings["clock.allowed-rates"])]
        try:
            quantum = int(settings.get("clock.quantum", ""))
        except ValueError:
            pass
    state = eq.load_state()
    return Snapshot(
        k7=k7,
        sink=sink,
        allowed_rates=allowed,
        latency=latency.current(settings) if settings else None,
        quantum=quantum,
        keepawake=keepawake.active_on_node(d) if d else None,
        keepawake_config=keepawake.is_enabled(),
        eq_profile=state.get("profile"),
        eq_on=bool(sink and sink.eq_active and state.get("enabled")),
        crossfeed=(state.get("crossfeed") if sink and sink.eq_active
                   and state.get("crossfeed") in eq.CROSSFEED else None),
        resample=resample.current(),
        exclusive=exclusive.is_on(d) if d else False,
        direct=exclusive.direct_user(k7.card_index) if k7 else None,
    )
