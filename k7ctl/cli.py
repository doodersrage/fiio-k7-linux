"""k7ctl command line."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import time

from . import (__version__, device, eq, exclusive, firmware, keepawake, latency, pipewire,
               rates, resample, snapshot)
from .device import format_rate


def _no_k7() -> int:
    name = device.display_name()
    hint = ("Is it plugged in and switched to USB input?\nCheck with: lsusb | grep -i fiio"
            if device.target_id() == device.DEFAULT_ID else
            "Is it plugged in? See: k7ctl devices")
    print(f"No {name} found. {hint}", file=sys.stderr)
    return 1


def _err(msg: str) -> int:
    print(f"k7ctl: {msg}", file=sys.stderr)
    return 1


# ------------------------------------------------------------------ status

def status_lines(s: snapshot.Snapshot) -> list[str]:
    k7 = s.k7
    st = k7.stream
    lines = [
        f"{k7.name}  (ALSA card {k7.card_index}, {k7.alsa_device})",
        f"  Firmware:     {k7.firmware or 'unknown'}",
        f"  USB:          {k7.usb_speed or 'unknown'}",
        f"  Playing:      {st.describe()}",
    ]
    if st.state != "closed" and st.format:
        lines.append(f"  Format:       {st.format}, {st.channels} ch")
    if st.momentary_rate and st.rate and abs(st.momentary_rate - st.rate) > st.rate * 0.01:
        lines.append(f"  Measured:     {st.momentary_rate} Hz")
    sink = s.sink
    if sink:
        for x in sink.playing:
            mark = ""
            if sink.rate and x.rate and not x.dsd and x.rate != sink.rate:
                mark = f"  -> resampled to {format_rate(sink.rate)}"
            rate = f", {x.rate_text}" if x.rate else ""
            lines.append(f"  Stream:       {x.app}{rate}{mark}")
        vol = f"{sink.volume * 100:.0f}%" if sink.volume is not None else "unknown"
        if sink.muted:
            vol += " (muted)"
        lines.append(f"  PW volume:    {vol}")
        if sink.eq_is_default:
            lines.append(f"  Default out:  {k7.name} + EQ")
        elif not sink.is_default:
            lines.append(f"  Default out:  not the {k7.name}")
    if s.allowed_rates is not None:
        lines.append(f"  PW rates:     {', '.join(format_rate(r) for r in s.allowed_rates)}")
    if s.latency:
        q = f" ({s.quantum} samples)" if s.quantum else ""
        lines.append(f"  Latency:      {s.latency}{q}")
    lines.append(f"  Keep awake:   {'on' if s.keepawake else 'off'}"
                 + (" (restart WirePlumber to apply)" if s.keepawake_config and not s.keepawake else ""))
    if s.exclusive:
        lines.append("  Exclusive:    on: PipeWire released it for direct players "
                     "(k7ctl exclusive off to undo)")
    if s.direct:
        lines.append(f"  Used by:      {s.direct}, directly (not through PipeWire)")
    if s.eq_on:
        lines.append(f"  EQ:           on ({s.eq_profile}), not bit-perfect")
    else:
        lines.append("  EQ:           off")
    lines.append(f"  Crossfeed:    {s.crossfeed + ', not bit-perfect' if s.crossfeed else 'off'}")
    if s.resample:
        lines.append(f"  Resampler:    {s.resample} (used only when rates have to be converted)")
    bp = s.bit_perfect
    if bp is not None:
        lines.append(f"  Bit-perfect:  {'yes' if bp else 'no'}")
    return lines


def advice(s: snapshot.Snapshot) -> list[str]:
    tips = list(s.why_resampled())
    if s.single_rate and not s.resampled:
        tips.append("PipeWire resamples everything to one rate. Fix: k7ctl rates enable")
    sink = s.sink
    if sink and sink.volume is not None and (sink.volume < 0.999 or sink.muted):
        tips.append("PipeWire volume below 100% costs resolution; use the K7's knob instead. "
                    "Fix: k7ctl volume-max")
    return tips


def waybar_json(s: snapshot.Snapshot) -> str:
    if s.k7:
        tooltip = "\n".join(status_lines(s) + [f"! {t}" for t in s.why_resampled()])
    else:
        tooltip = f"{device.display_name()} not connected"
    return json.dumps({"text": s.short(), "tooltip": tooltip,
                       "class": s.css_class(), "alt": s.css_class()})


def json_status(s: snapshot.Snapshot) -> str:
    sink = s.sink
    return json.dumps({
        "connected": s.connected,
        "device": s.k7.to_dict() if s.k7 else None,
        "bit_perfect": s.bit_perfect,
        "pipewire": {
            "sink": ({k: v for k, v in sink.__dict__.items() if k != "streams"} if sink else None),
            "streams": [x.__dict__ for x in sink.streams] if sink else [],
            "resampled": [x.__dict__ for x in s.resampled],
            "allowed_rates": s.allowed_rates,
            "rates_config_installed": rates.is_enabled(),
            "latency": s.latency,
            "quantum": s.quantum,
        },
        "keepawake": s.keepawake,
        "eq": {"on": s.eq_on, "profile": s.eq_profile},
        "crossfeed": s.crossfeed,
        "resample": s.resample,
    }, indent=2)


def cmd_status(args) -> int:
    fmt = "json" if args.json else args.format
    last = None
    while True:
        s = snapshot.take()
        if fmt == "json":
            out = json_status(s)
        elif fmt == "waybar":
            out = waybar_json(s)
        elif fmt == "short":
            out = s.short()
        elif s.k7 is None:
            out = f"No {device.display_name()} connected."
        else:
            lines = status_lines(s)
            tips = advice(s)
            if tips and not args.watch:
                lines += [""] + [f"Tip: {t}" for t in tips]
            out = "\n".join(lines)

        if not args.watch:
            print(out)
            return 0 if s.k7 else 1
        if out != last:
            if fmt == "text":
                stamp = time.strftime("%H:%M:%S")
                print(f"[{stamp}] " + out.replace("\n", "\n           "), flush=True)
            else:
                print(out.replace("\n", " ") if fmt == "waybar" else out, flush=True)
            last = out
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


# ------------------------------------------------------------------- rates

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
    print("Allowed now:   " + (", ".join(format_rate(r) for r in allowed)
                               if allowed else "unknown (PipeWire not running?)"))
    return 0


# --------------------------------------------------------------- latency

def cmd_latency(args) -> int:
    if args.preset is None:
        cur = latency.current()
        print(f"Current: {cur or 'unknown'}")
        for name, (v, desc) in latency.PRESETS.items():
            mark = "*" if name == cur else " "
            print(f" {mark} {name:12s} {v[0]:5d} samples  {desc}")
        return 0
    live = latency.apply(args.preset)
    q = latency.PRESETS[args.preset][0][0]
    print(f"Latency preset '{args.preset}' ({q} samples)"
          + (" applied." if live else " saved; restart PipeWire to apply."))
    if args.preset != "default":
        print(f"Saved in {latency.conf_path()}")
    return 0


# ------------------------------------------------------------- keepawake

def cmd_keepawake(args) -> int:
    if args.action == "on":
        if not args.no_restart:
            print("Restarting WirePlumber: audio stops for about a second.")
        path, restarted = keepawake.enable(restart=not args.no_restart)
        print(f"Wrote {path}")
        print("Active." if restarted else
              "Takes effect when WirePlumber restarts: systemctl --user restart wireplumber")
        return 0
    if args.action == "off":
        if not args.no_restart and keepawake.is_enabled():
            print("Restarting WirePlumber: audio stops for about a second.")
        removed, restarted = keepawake.disable(restart=not args.no_restart)
        if not removed:
            print("Keep-awake wasn't on.")
        else:
            print("Removed. The K7 suspends after a few idle seconds again."
                  + ("" if restarted else " (after WirePlumber restarts)"))
        return 0
    d = pipewire.dump()
    live = keepawake.active_on_node(d) if d else None
    print(f"Config: {'installed' if keepawake.is_enabled() else 'not installed'} "
          f"({keepawake.conf_path()})")
    print(f"Active: {'yes' if live else 'no' if live is False else 'unknown (K7 not in PipeWire)'}")
    return 0


# ---------------------------------------------------------------------- eq

def cmd_eq(args) -> int:
    a = args.eq_action or "status"
    try:
        if a == "_run":
            return eq.run_daemon()
        if a == "status":
            s = snapshot.take(with_volume=False)
            prof = eq.selected_profile()
            print(f"EQ:       {'on' if s.eq_on else 'off'}"
                  + (" (service running, waiting for the K7)" if not s.eq_on and eq.is_running() else ""))
            print(f"Profile:  {prof or 'none selected'}")
            profiles = eq.list_profiles()
            print(f"Saved:    {len(profiles)} profile(s) in {eq.profiles_dir()}")
            return 0
        if a == "list":
            sel = eq.selected_profile()
            profiles = eq.list_profiles()
            if not profiles:
                print('No profiles yet. Get one with: k7ctl eq search "<headphone name>"')
            for p in profiles:
                print(("* " if p == sel else "  ") + p)
            return 0
        if a == "search":
            hits = eq.search(" ".join(args.query), eq.autoeq_index(refresh=args.refresh))
            if not hits:
                print("No AutoEQ profiles match.")
                return 1
            for i, h in enumerate(hits[:args.limit], 1):
                print(f"{i:3d}. {h.name}  [{h.source}]")
            if len(hits) > args.limit:
                print(f"     … {len(hits) - args.limit} more; be more specific or use --limit")
            print('\nDownload with: k7ctl eq get "<name>" [--source <source>]')
            return 0
        if a == "get":
            query = " ".join(args.query)
            hits = eq.search(query)
            exact = [h for h in hits if h.name.lower() == query.lower()] or hits
            if args.source:
                exact = [h for h in exact if args.source.lower() in h.source.lower()]
            if not exact:
                return _err(f"no AutoEQ profile matches {query!r}; try: k7ctl eq search {query}")
            names = {h.name for h in exact}
            if len(names) > 1:
                print("Several headphones match; use the exact name:")
                for n in sorted(names)[:20]:
                    print(f"  {n}")
                return 1
            entry = exact[0]
            name, path = eq.download(entry, args.name)
            print(f"Saved '{name}' (measured by {entry.source}) to {path}")
            if args.on:
                eq.enable(name)
                print(f"EQ on: {name}")
            else:
                print(f'Turn it on with: k7ctl eq on "{name}"')
            return 0
        if a == "import":
            name, path = eq.import_file(args.file, args.name)
            print(f"Imported '{name}' to {path}")
            return 0
        if a == "remove":
            if eq.delete_profile(args.profile):
                print(f"Removed '{args.profile}'.")
                return 0
            return _err(f"no profile called {args.profile!r}")
        if a == "on":
            if not device.find():
                return _no_k7()
            name = eq.enable(args.profile)
            print(f"EQ on: {name}. '{device.display_name()} + EQ' is now the default output.")
            print("Note: with EQ on, playback is processed (not bit-perfect).")
            return 0
        if a == "toggle":
            if snapshot.take(with_volume=False).eq_on:
                eq.disable()
                print("EQ off.")
            else:
                if not device.find():
                    return _no_k7()
                print(f"EQ on: {eq.enable()}.")
            return 0
        if a == "off":
            eq.disable()
            print("EQ off. The K7 is the default output again.")
            return 0
    except eq.EqError as e:
        return _err(str(e))
    except OSError as e:
        return _err(str(e))
    return _err(f"unknown eq action {a}")


# ------------------------------------------------------------- the rest

def cmd_volume_max(args) -> int:
    sink = pipewire.find_sink()
    if not sink:
        return _err("the K7 isn't showing up in PipeWire.")
    if not pipewire.set_full_volume(sink):
        return _err("couldn't set the volume (is wpctl installed?).")
    print(f"{sink.description}: volume 100%, unmuted. Turn the K7's knob down "
          "before playing anything.")
    return 0


def cmd_setup(args) -> int:
    """Everything for bit-perfect playback in one go."""
    if not device.find():
        return _no_k7()
    rc = cmd_rates(argparse.Namespace(action="enable"))
    sink = pipewire.find_sink()
    if sink:
        if sink.eq_active:
            print("Note: EQ is on, so playback isn't bit-perfect. Turn it off with: k7ctl eq off")
        elif not sink.is_default and pipewire.make_default(sink):
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
    print(firmware.update_help(k7))
    if args.open:
        firmware.open_download_page()
    return 0


def cmd_crossfeed(args) -> int:
    if args.preset is None:
        cur = eq.crossfeed_preset()
        running = snapshot.take(with_volume=False).crossfeed
        print(f"Crossfeed: {cur or 'off'}" + (" (running)" if running else
                                              " (not running: is the DAC connected?)" if cur else ""))
        for name, (fc, feed) in eq.CROSSFEED.items():
            mark = "*" if name == cur else " "
            print(f" {mark} {name:7s} {fc} Hz, crossfed bass {feed} dB below the direct sound")
        return 0
    try:
        if args.preset != "off" and not device.find():
            return _no_k7()
        result = eq.set_crossfeed(args.preset)
    except eq.EqError as e:
        return _err(str(e))
    if result:
        print(f"Crossfeed on: {result}. Playback is processed (not bit-perfect).")
    else:
        print("Crossfeed off.")
    return 0


def cmd_resample(args) -> int:
    if args.preset is None:
        cur = resample.current()
        print(f"Resampler quality: {cur} ({resample.quality()})")
        for name, (q, desc) in resample.PRESETS.items():
            mark = "*" if name == cur else " "
            print(f" {mark} {name:8s} {q:2d}  {desc}")
        print("Used only when PipeWire has to convert sample rates.")
        return 0
    if args.restart:
        print("Restarting PipeWire's PulseAudio service: sound from Firefox and other apps stops briefly.")
    restarted = resample.apply(args.preset, restart=args.restart)
    print(f"Resampler quality: {args.preset} ({resample.PRESETS[args.preset][0]}).")
    if eq.restart_if_running():
        print("The EQ/crossfeed uses it now.")
    if restarted:
        print("Applied to PulseAudio apps (Firefox, Spotify…) now; other apps when they next start.")
    else:
        print("Applies to apps started from now on. To apply it to running apps now: "
              "k7ctl resample " + args.preset + " --restart")
    return 0


def cmd_exclusive(args) -> int:
    a = args.action
    try:
        if a == "on":
            if not device.find():
                return _no_k7()
            print(exclusive.enable())
            return 0
        if a == "off":
            print(exclusive.disable())
            return 0
    except RuntimeError as e:
        return _err(str(e))
    s = snapshot.take(with_volume=False)
    print(f"Exclusive: {'on (PipeWire released it)' if s.exclusive else 'off (PipeWire uses it)'}")
    if s.direct:
        print(f"Used directly by: {s.direct}")
    return 0


def cmd_devices(args) -> int:
    cards = device.list_usb_cards()
    want = device.target_id()
    conf = device.configured_id()
    if not cards:
        print("No USB sound cards found.")
        return 1
    for c in cards:
        mark = "*" if c.usb_id == want else " "
        kind = "" if c.playback else "  (no playback)"
        print(f"{mark} {c.usb_id}  {c.name}  [card {c.card_index}: {c.card_id}]{kind}")
    print(f"\nUsing: {'auto (the FiiO K7)' if conf == 'auto' else conf}. "
          "Change with: k7ctl use <vvvv:pppp|auto>")
    return 0


def cmd_use(args) -> int:
    from . import actions
    try:
        for m in actions.use_device(args.device):
            print(m)
    except ValueError as e:
        return _err(str(e))
    return 0


def cmd_diag(args) -> int:
    from . import diagnostics
    return diagnostics.main()


def cmd_tray(args) -> int:
    try:
        from .tray import main as tray_main
    except ImportError as e:
        return _err(str(e))
    return tray_main()


def cmd_gui(args) -> int:
    try:
        from .window import main as window_main
    except ImportError as e:
        return _err(str(e))
    return window_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="k7ctl", description="FiiO K7 (and other USB DAC) status and setup for Linux")
    p.add_argument("--version", action="version", version=f"k7ctl {__version__}")
    p.add_argument("--notify", action="store_true",
                   help="show the result as a desktop notification (for launchers and shortcuts)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    s = sub.add_parser("status", help="show what the K7 is playing (default)")
    s.add_argument("--format", choices=["text", "json", "waybar", "short"], default="text",
                   help="output format; waybar/short are for status bars")
    s.add_argument("--json", action="store_true", help="same as --format json")
    s.add_argument("-w", "--watch", action="store_true", help="keep running, print changes")
    s.add_argument("--interval", type=float, default=1.0, help="seconds between checks with --watch")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("rates", help="let PipeWire use the music's own sample rate")
    r.add_argument("action", nargs="?", choices=["enable", "disable", "status"], default="status")
    r.set_defaults(func=cmd_rates)

    la = sub.add_parser("latency", help="buffer size presets (fix crackles / lower latency)")
    la.add_argument("preset", nargs="?", choices=list(latency.PRESETS))
    la.set_defaults(func=cmd_latency)

    k = sub.add_parser("keepawake", help="stop the K7 suspending when idle (no cut-off sounds)")
    k.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    k.add_argument("--no-restart", action="store_true",
                   help="don't restart WirePlumber now (applies at next login)")
    k.set_defaults(func=cmd_keepawake)

    e = sub.add_parser("eq", help="headphone EQ (AutoEQ profiles)")
    esub = e.add_subparsers(dest="eq_action", metavar="ACTION")
    esub.add_parser("status", help="is EQ on, which profile")
    esub.add_parser("list", help="saved profiles")
    es = esub.add_parser("search", help="search AutoEQ's headphone profiles")
    es.add_argument("query", nargs="+")
    es.add_argument("--limit", type=int, default=25)
    es.add_argument("--refresh", action="store_true", help="re-download the AutoEQ index")
    eg = esub.add_parser("get", help="download an AutoEQ profile")
    eg.add_argument("query", nargs="+", help="headphone name, as shown by search")
    eg.add_argument("--source", help="measurement source, e.g. oratory1990 or crinacle")
    eg.add_argument("--name", help="save under this name")
    eg.add_argument("--on", action="store_true", help="turn EQ on with it")
    ei = esub.add_parser("import", help="import an Equalizer APO / ParametricEQ.txt file")
    ei.add_argument("file")
    ei.add_argument("--name")
    er = esub.add_parser("remove", help="delete a saved profile")
    er.add_argument("profile")
    eo = esub.add_parser("on", help="turn EQ on (optionally with a profile)")
    eo.add_argument("profile", nargs="?")
    esub.add_parser("off", help="turn EQ off")
    esub.add_parser("toggle", help="turn EQ on or off")
    esub.add_parser("_run")  # used by the systemd service
    e.set_defaults(func=cmd_eq)

    v = sub.add_parser("volume-max", help="set PipeWire's K7 volume to 100%% (use the knob instead)")
    v.set_defaults(func=cmd_volume_max)

    st = sub.add_parser("setup", help="rates enable + default output + volume-max")
    st.add_argument("-y", "--yes", action="store_true", help="don't ask before setting volume to 100%%")
    st.set_defaults(func=cmd_setup)

    f = sub.add_parser("firmware", help="show the firmware version and how to update it")
    f.add_argument("--open", action="store_true", help="open FiiO's firmware download page")
    f.set_defaults(func=cmd_firmware)

    cf = sub.add_parser("crossfeed", help="headphone crossfeed (less 'inside your head' sound)")
    cf.add_argument("preset", nargs="?", choices=["off", *eq.CROSSFEED])
    cf.set_defaults(func=cmd_crossfeed)

    rs = sub.add_parser("resample", help="resampler quality, when rates have to be converted")
    rs.add_argument("preset", nargs="?", choices=list(resample.PRESETS))
    rs.add_argument("--restart", action="store_true",
                    help="restart PipeWire's PulseAudio service so running apps use it now")
    rs.set_defaults(func=cmd_resample)

    ex = sub.add_parser("exclusive", help="release the DAC from PipeWire for direct players (native DSD)")
    ex.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    ex.set_defaults(func=cmd_exclusive)

    dv = sub.add_parser("devices", help="list USB sound cards (to use another DAC)")
    dv.set_defaults(func=cmd_devices)

    us = sub.add_parser("use", help="choose the DAC: a USB id from 'devices', or auto (the K7)")
    us.add_argument("device", help="vvvv:pppp or auto")
    us.set_defaults(func=cmd_use)

    dg = sub.add_parser("diag", help="print a diagnostics report for bug reports")
    dg.set_defaults(func=cmd_diag)

    g = sub.add_parser("gui", help="open the FiiO K7 window")
    g.set_defaults(func=cmd_gui)

    t = sub.add_parser("tray", help="system tray icon (click it to open the window)")
    t.set_defaults(func=cmd_tray)
    return p


def notify(summary: str, body: str, error: bool = False) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(["notify-send", "--app-name=k7ctl", "--icon",
                        "dialog-error" if error else "audio-card", summary, body], timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args([*argv, "status"])
    if not args.notify:
        return args.func(args)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = args.func(args)
    text = (out.getvalue() + err.getvalue()).strip()
    if not notify(device.display_name(), text or ("Done." if rc == 0 else "Failed."), rc != 0):
        print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
