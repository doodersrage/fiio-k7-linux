"""One read of everything: the device, PipeWire, and k7ctl's settings.

Shared by the command line, status bar output and the tray icon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import device, eq, keepawake, latency, pipewire, pwconf
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
    def single_rate(self) -> bool:
        return self.allowed_rates is not None and len(self.allowed_rates) <= 1

    @property
    def bit_perfect(self) -> bool | None:
        """True/False while playing; None when idle or unknown."""
        if not self.playing or not self.sink:
            return None
        if self.eq_on or self.resampled:
            return False
        if self.sink.volume is not None and (self.sink.volume < 0.999 or self.sink.muted):
            return False
        return True

    def why_resampled(self) -> list[str]:
        out = []
        if not self.sink or not self.sink.rate:
            return out
        holders = [s.app for s in self.sink.holders() if not s.is_eq]
        for s in self.resampled:
            line = (f"{s.app} is {format_rate(s.rate)} but the K7 is running at "
                    f"{format_rate(self.sink.rate)}")
            if self.single_rate:
                line += " (PipeWire only allows one rate: k7ctl rates enable)"
            elif holders:
                line += f" because {', '.join(sorted(set(holders)))} started first"
            out.append(line)
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
        return text

    def css_class(self) -> str:
        if not self.k7:
            return "disconnected"
        if self.eq_on:
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
        eq_on=bool(sink and sink.eq_active),
    )
