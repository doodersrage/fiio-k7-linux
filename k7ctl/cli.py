"""k7ctl command line."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__, device, firmware, pipewire, rates


def _no_k7() -> int:
    print("No FiiO K7 found. Is it plugged in and switched to USB input?\n"
          "Check with: lsusb | grep -i fiio", file=sys.stderr)
    return 1


def _status_lines(k7: device.K7, sink: pipewire.Sink | None,
                  allowed: list[int] | None) -> list[str]:
    s = k7.stream
    lines = [
        f"{k7.name}  (ALSA card {k7.card_index}, {k7.alsa_device})",
        f"  Firmware:     {k7.firmware or 'unknown'}",
        f"  USB:          {k7.usb_speed or 'unknown'}",
        f"  Playing:      {s.describe()}",
    ]
    if s.state != "closed" and s.format:
        lines.append(f"  Format:       {s.format}, {s.channels} ch")
    if s.momentary_rate and s.rate and abs(s.momentary_rate - s.rate) > s.rate * 0.01:
        lines.append(f"  Measured:     {s.momentary_rate} Hz")
    if sink:
        vol = f"{sink.volume * 100:.0f}%" if sink.volume is not None else "unknown"
        if sink.muted:
            vol += " (muted)"
        lines.append(f"  PW volume:    {vol}{'' if sink.is_default else '  (not the default output)'}")
    if allowed is not None:
        lines.append(f"  PW rates:     {', '.join(device.format_rate(r) for r in allowed)}")
    return lines


def _advice(k7: device.K7, sink: pipewire.Sink | None, allowed: list[int] | None) -> list[str]:
    tips = []
    if allowed is not None and len(allowed) <= 1:
        tips.append("PipeWire resamples everything to one rate. Fix: k7ctl rates enable")
    if sink and sink.volume is not None and (sink.volume < 0.999 or sink.muted):
        tips.append("PipeWire volume below 100% costs resolution; use the K7's knob instead. "
                    "Fix: k7ctl volume-max")
    return tips


def cmd_status(args) -> int:
    def snapshot():
        k7 = device.find()
        sink = pipewire.find_sink() if k7 else None
        allowed = rates.live_allowed_rates()
        return k7, sink, allowed

    if args.json:
        k7, sink, allowed = snapshot()
        data = {
            "connected": k7 is not None,
            "device": k7.to_dict() if k7 else None,
            "pipewire": {
                "sink": sink.__dict__ if sink else None,
                "allowed_rates": allowed,
                "rates_config_installed": rates.is_enabled(),
            },
        }
        print(json.dumps(data, indent=2))
        return 0 if k7 else 1

    last = None
    while True:
        k7, sink, allowed = snapshot()
        if k7 is None:
            text = "No FiiO K7 connected."
        else:
            lines = _status_lines(k7, sink, allowed)
            tips = _advice(k7, sink, allowed)
            if tips and not args.watch:
                lines += [""] + [f"Tip: {t}" for t in tips]
            text = "\n".join(lines)
        if not args.watch:
            print(text)
            return 0 if k7 else 1
        if text != last:
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] " + text.replace("\n", "\n           "), flush=True)
            last = text
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def cmd_rates(args) -> int:
    if args.action == "enable":
        path, live = rates.enable()
        print(f"Wrote {path}")
        print("Applied to the running PipeWire." if live else
              "Couldn't apply live (is pw-metadata installed?); it takes effect "
              "after: systemctl --user restart pipewire")
        print("PipeWire now switches the K7 to the rate of what's playing "
              "(when nothing else is playing at a different rate).")
        return 0
    if args.action == "disable":
        removed, live = rates.disable()
        print("Removed " + str(rates.conf_path()) if removed else "k7ctl's config wasn't installed.")
        if live:
            print("PipeWire is back to 48 kHz only.")
        return 0
    allowed = rates.live_allowed_rates()
    print(f"Config file:   {'installed' if rates.is_enabled() else 'not installed'} "
          f"({rates.conf_path()})")
    print("Allowed now:   " + (", ".join(device.format_rate(r) for r in allowed)
                               if allowed else "unknown (PipeWire not running?)"))
    return 0


def cmd_volume_max(args) -> int:
    sink = pipewire.find_sink()
    if not sink:
        print("The K7 isn't showing up in PipeWire.", file=sys.stderr)
        return 1
    if not pipewire.set_full_volume(sink):
        print("Couldn't set the volume (is wpctl installed?).", file=sys.stderr)
        return 1
    print(f"{sink.description}: volume 100%, unmuted. Turn the K7's knob down "
          "before playing anything.")
    return 0


def cmd_setup(args) -> int:
    """Everything for bit-perfect playback in one go."""
    k7 = device.find()
    if not k7:
        return _no_k7()
    rc = cmd_rates(argparse.Namespace(action="enable"))
    sink = pipewire.find_sink()
    if sink:
        if not sink.is_default and pipewire.make_default(sink):
            print("Made the K7 the default output.")
        if sink.volume is None or sink.volume < 0.999 or sink.muted:
            print("\nWARNING: this sets PipeWire's volume for the K7 to 100%. "
                  "Turn the K7's volume knob down first.")
            if not args.yes:
                try:
                    if input("Continue? [y/N] ").strip().lower() != "y":
                        print("Left the volume alone.")
                        return rc
                except EOFError:
                    return rc
            rc |= cmd_volume_max(args)
    return rc


def cmd_firmware(args) -> int:
    k7 = device.find()
    if not k7:
        return _no_k7()
    print(f"Installed firmware: {k7.firmware or 'unknown'}")
    dfu = firmware.read_dfu(k7.usb_path)
    if dfu:
        print(f"Update interface:   USB DFU, {dfu.transfer_size}-byte blocks, "
              f"{'detaches itself' if dfu.will_detach else 'needs a host-driven reset'}")
    print()
    print(firmware.UPDATE_HELP)
    return 0


def cmd_tray(args) -> int:
    from .tray import main as tray_main
    return tray_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="k7ctl", description="FiiO K7 status and setup for Linux")
    p.add_argument("--version", action="version", version=f"k7ctl {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("status", help="show what the K7 is playing (default)")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.add_argument("-w", "--watch", action="store_true", help="keep running, print changes")
    s.add_argument("--interval", type=float, default=1.0, help="seconds between checks with --watch")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("rates", help="let PipeWire use the music's own sample rate")
    r.add_argument("action", nargs="?", choices=["enable", "disable", "status"], default="status")
    r.set_defaults(func=cmd_rates)

    v = sub.add_parser("volume-max", help="set PipeWire's K7 volume to 100%% (use the knob instead)")
    v.set_defaults(func=cmd_volume_max)

    st = sub.add_parser("setup", help="rates enable + default output + volume-max")
    st.add_argument("-y", "--yes", action="store_true", help="don't ask before setting volume to 100%%")
    st.set_defaults(func=cmd_setup)

    f = sub.add_parser("firmware", help="show the firmware version and how to update it")
    f.set_defaults(func=cmd_firmware)

    t = sub.add_parser("tray", help="system tray icon")
    t.set_defaults(func=cmd_tray)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        args = build_parser().parse_args(["status", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
