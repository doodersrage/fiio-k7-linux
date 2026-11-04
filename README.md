# fiio-k7-linux (k7ctl): FiiO K7 on Linux

FiiO's software for the K7 DAC/headphone amp is Windows-only. This project gives Linux the parts that matter:

- **Status**: what the K7 is receiving right now (sample rate, bit depth, PCM or DSD), firmware version, connection.
- **Bit-perfect setup**: by default PipeWire resamples *everything* to 48 kHz. `k7ctl` lets it switch the K7 to the
  native rate of what's playing (44.1 kHz up to 384 kHz) and checks that PipeWire's volume isn't throwing away resolution.
- **System tray icon** (KDE, GNOME with the AppIndicator extension, and others) with the same information and quick actions.
- A command line tool with JSON output for scripts and status bars.

## What the K7 does and doesn't expose over USB

The K7 is a standard USB Audio Class 2 device, so **sound works on Linux with no driver**. All formats work:
PCM 16/32-bit at 44.1 to 384 kHz, and native DSD up to DSD256.

Over USB it has only two things: the audio stream and a firmware-update (DFU) interface.
**It has no volume, gain, mute or input/output controls over USB.** The volume knob, gain switch and
input/output selector are physical only, so no Linux (or Windows) software can change them. Windows needs FiiO's
driver for ASIO; Linux's USB audio driver and PipeWire already cover that.

## Install

### Arch Linux and derivatives (EndeavourOS, Manjaro, CachyOS)

```sh
sudo pacman -S --needed base-devel git python-pyqt6
git clone https://github.com/doodersrage/fiio-k7-linux
cd fiio-k7-linux
makepkg -si
```

### Anything else

```sh
pipx install "git+https://github.com/doodersrage/fiio-k7-linux"           # command line only
pipx install "k7ctl[tray] @ git+https://github.com/doodersrage/fiio-k7-linux"   # with the tray icon
```

Needs Python 3.9+, PipeWire with WirePlumber (the default on current Fedora, Ubuntu, Debian, Arch), and
for the tray icon PyQt6 or PySide6. No root access or udev rules needed: everything is read from `/proc` and `/sys`
or done through PipeWire as your user.

## Quick start

```sh
k7ctl            # status
k7ctl setup      # bit-perfect setup: native sample rates, K7 as default output, PipeWire volume 100%
k7ctl tray       # tray icon (tick "Start at login" in its menu to keep it)
```

> **Turn the K7's knob down before `k7ctl setup`.** If PipeWire's volume for the K7 was below 100%, the setup raises it to 100%,
> so the same knob position will be louder.

## Commands

| Command | What it does |
|---|---|
| `k7ctl status` | What's playing, firmware, PipeWire volume and allowed rates, plus tips. `--json` for scripts, `--watch` to follow changes. |
| `k7ctl rates enable` | Let PipeWire run at the music's own rate. Writes `~/.config/pipewire/pipewire.conf.d/50-k7ctl-samplerates.conf` and applies it immediately (no restart). |
| `k7ctl rates disable` | Remove that file and go back to 48 kHz only. |
| `k7ctl rates` | Show whether it's enabled and what PipeWire currently allows. |
| `k7ctl volume-max` | Set PipeWire's volume for the K7 to 100% and unmute. |
| `k7ctl setup` | `rates enable`, make the K7 the default output, and `volume-max` (asks first; `-y` to skip asking). |
| `k7ctl firmware` | Installed firmware version and how to update it. |
| `k7ctl tray` | System tray icon. |

Example:

```
$ k7ctl
FiiO K7  (ALSA card 1, hw:CARD=K7,DEV=0)
  Firmware:     1.22
  USB:          480 Mbit/s
  Playing:      44.1 kHz / 32-bit
  Format:       S32_LE, 2 ch
  PW volume:    100%
  PW rates:     44.1 kHz, 48 kHz, 88.2 kHz, 96 kHz, 176.4 kHz, 192 kHz, 352.8 kHz, 384 kHz
```

"32-bit" is the container PipeWire sends; a 16- or 24-bit file arrives unchanged inside it.

## Notes on bit-perfect playback

- **Rate switching only happens when the K7 isn't already busy at another rate.** If a browser tab is playing 48 kHz
  audio and you start a 44.1 kHz track, PipeWire keeps 48 kHz and resamples the track. Watch `k7ctl status --watch`
  to see what the K7 actually receives.
- The allowed-rates setting is PipeWire-wide. Other outputs that can't do the chosen rate get resampled by PipeWire as
  before, so nothing breaks.
- **Volume**: the K7 has no hardware volume over USB, so PipeWire's volume slider for it is software volume.
  Anything under 100% reduces the signal digitally. Leave it at 100% and use the knob.

## DSD

PipeWire plays PCM. For native DSD (DSF/DFF files sent to the DAC as DSD, not converted):

- **`pw-dsdplay file.dsf`** (comes with PipeWire) plays a DSD file natively through PipeWire. *Not yet tested with the K7.*
- **MPD**: send straight to the DAC with ALSA:

  ```
  audio_output {
      type            "alsa"
      name            "FiiO K7 (direct, native DSD)"
      device          "hw:CARD=K7,DEV=0"
      dop             "no"
      mixer_type      "none"
      auto_resample   "no"
      auto_format     "no"
      auto_channels   "no"
  }
  ```

  `hw:` access is exclusive: if PipeWire is using the K7 at that moment, MPD reports "Device or resource busy". PipeWire
  releases an idle device after a few seconds.
- Other players with a direct ALSA/"exclusive" output (Strawberry, DeaDBeeF, Audacious with the ALSA plugin) work the same
  way with device `hw:CARD=K7,DEV=0`.

`k7ctl status` shows e.g. `DSD64 (2.822 MHz, native)` when DSD is playing.

## Firmware updates

k7ctl shows the installed version but **does not flash firmware**. FiiO's update format isn't documented, and a failed
flash can leave the DAC unusable. To update, run FiiO's Windows updater, either on a Windows PC or in a Windows virtual
machine (virt-manager/QEMU, VirtualBox) with the K7 (`2972:0047`) passed through as a USB device. Keep the K7 powered
for the whole update.

## Uninstall

```sh
k7ctl rates disable                      # remove the PipeWire config
rm -f ~/.config/autostart/k7ctl-tray.desktop
sudo pacman -R k7ctl                     # or: pipx uninstall k7ctl
```

## Development

```sh
python -m k7ctl status              # run from the checkout
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Tested with a FiiO K7 (USB `2972:0047`, firmware 1.22), PipeWire 1.6, KDE Plasma on Wayland.

## License

MIT
