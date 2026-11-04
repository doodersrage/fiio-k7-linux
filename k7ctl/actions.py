"""Multi-step actions shared by the command line and the window."""

from __future__ import annotations

from . import device, eq, keepawake, settings


def use_device(value: str) -> list[str]:
    """Switch k7ctl to another DAC (a USB id or "auto"). Returns messages to show.

    Keep-awake and the EQ are moved to the new device if they're on.
    Raises ValueError for an invalid id.
    """
    new = device.normalize_id(value)
    settings.set(device=new)
    target = device.target_id()
    found = device.find()
    msgs = [f"Using {found.name if found else device.display_name()} ({target})"
            + ("" if found else ", not connected right now") + "."]
    if keepawake.is_enabled() and keepawake.configured_for() != target:
        keepawake.enable()
        msgs.append("Keep-awake now applies to it (WirePlumber was restarted).")
    if eq.is_running():
        try:
            eq.enable()
            msgs.append("The EQ now plays through it.")
        except eq.EqError as e:
            msgs.append(f"EQ: {e}")
    return msgs
