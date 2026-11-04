"""Keep the K7 from being suspended when idle.

WirePlumber closes an output after ~5 seconds of silence. Reopening it takes
a moment, so on many USB DACs the start of the next sound (a notification,
the first notes of a track) is cut off or pops. This WirePlumber rule turns
suspend off for the K7 only. It takes effect when WirePlumber restarts, which
interrupts audio for about a second.
"""

from __future__ import annotations

from pathlib import Path

from . import pipewire, pwconf

CONF_NAME = "50-k7ctl-keepawake.conf"
NODE_PATTERN = "~alsa_output.usb-.*FiiO_K7.*"

RULE = f"""\
# Keep the FiiO K7 open when idle so the start of sounds isn't cut off.
# Remove with: k7ctl keepawake off
monitor.alsa.rules = [
  {{
    matches = [
      {{ node.name = "{NODE_PATTERN}" }}
    ]
    actions = {{
      update-props = {{
        session.suspend-timeout-seconds = 0
      }}
    }}
  }}
]
"""


def conf_path() -> Path:
    return pwconf.wireplumber_dropin(CONF_NAME)


def is_enabled() -> bool:
    return conf_path().is_file()


def restart_wireplumber() -> bool:
    """Restart WirePlumber, keeping the K7 (or its EQ) as the default output."""
    d = pipewire.dump()
    was_default = pipewire.k7_is_default(d) if d else False
    default_name = pipewire.parse_default_sink(d) if d else None
    ok = pwconf.systemctl_user("restart", "wireplumber")
    if ok and was_default:
        pipewire.wait_and_set_default(
            pipewire.EQ_NODE if default_name == pipewire.EQ_NODE else None)
    return ok


def enable(restart: bool = True) -> tuple[Path, bool]:
    path = pwconf.write(conf_path(), RULE)
    return path, (restart_wireplumber() if restart else False)


def disable(restart: bool = True) -> tuple[bool, bool]:
    removed = pwconf.remove(conf_path())
    return removed, (restart_wireplumber() if restart and removed else False)


def active_on_node(pw_dump: list) -> bool | None:
    """Whether the running K7 sink has suspend disabled (None if no sink)."""
    for obj in pw_dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Sink" and "FiiO_K7" in str(props.get("node.name", "")):
            return str(props.get("session.suspend-timeout-seconds")) == "0"
    return None
