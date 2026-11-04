# Changelog

## 0.2.0

- **Why isn't it bit-perfect?** `k7ctl status` now lists every app playing to the K7 with its sample rate,
  marks streams that are being resampled, and says which app is holding the K7 at the other rate.
  A `Bit-perfect: yes/no` line sums it up. The tray shows the same as a warning.
- **Headphone EQ**: `k7ctl eq search/get/import/on/off` with profiles from AutoEQ (thousands of headphones)
  or any Equalizer APO file. The preamp is applied, so boosts don't clip. Runs as a systemd user service
  that follows the K7 if its PipeWire node is renamed. Also in the tray, including "Find my headphones…".
- **Keep awake**: `k7ctl keepawake on` stops WirePlumber suspending the K7 when idle, so the start of
  sounds isn't cut off. Restarting WirePlumber keeps the K7 (or its EQ) as the default output.
- **Latency presets**: `k7ctl latency stable|low-latency|default`, applied live.
- **Settings window**: `k7ctl gui` (and "FiiO K7" in the app launcher) with the status, the streams playing to the K7,
  every setting, and AutoEQ search and download. The tray opens it on click; slow work (WirePlumber restarts,
  downloads) runs in the background, so neither the window nor the tray freezes.
- **Status bars**: `k7ctl status --format waybar|short [--watch]`, with example Waybar and Polybar configs.
- Bash, zsh and fish completions; a man page.
- Packages for Debian/Ubuntu (.deb), Fedora (.rpm) and Arch, built and tested in CI.

## 0.1.0

- First release: K7 status (sample rate, format, firmware), native sample rates in PipeWire
  (`k7ctl rates enable`), `volume-max`, `setup`, firmware info and a system tray icon.
