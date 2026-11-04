#!/bin/sh
# Install (or remove) everything except the Python package itself.
#   sudo ./install-files.sh /               install into the system
#   sudo ./install-files.sh --uninstall /   remove those files again
# Also used by the PKGBUILDs, debian/rules and the RPM spec to stage files into the package.
set -e
uninstall=
if [ "$1" = "--uninstall" ]; then uninstall=1; shift; fi
root=${1:?usage: install-files.sh [--uninstall] DESTDIR}
src=$(dirname "$0")

# source  destination
files="
completions/k7ctl.bash          usr/share/bash-completion/completions/k7ctl
completions/_k7ctl              usr/share/zsh/site-functions/_k7ctl
completions/k7ctl.fish          usr/share/fish/vendor_completions.d/k7ctl.fish
data/k7ctl.desktop              usr/share/applications/k7ctl.desktop
data/k7ctl-tray.desktop         usr/share/applications/k7ctl-tray.desktop
data/io.github.doodersrage.k7ctl.metainfo.xml usr/share/metainfo/io.github.doodersrage.k7ctl.metainfo.xml
docs/k7ctl.1                    usr/share/man/man1/k7ctl.1
README.md                       usr/share/doc/k7ctl/README.md
contrib/polybar.ini             usr/share/doc/k7ctl/polybar.ini
contrib/waybar/config.jsonc     usr/share/doc/k7ctl/waybar/config.jsonc
contrib/waybar/style.css        usr/share/doc/k7ctl/waybar/style.css
LICENSE                         usr/share/licenses/k7ctl/LICENSE
"

echo "$files" | while read -r from to; do
    [ -n "$from" ] || continue
    if [ -n "$uninstall" ]; then
        rm -f "$root/$to"
    else
        install -Dm644 "$src/$from" "$root/$to"
    fi
done
if [ -n "$uninstall" ]; then
    rmdir "$root/usr/share/doc/k7ctl/waybar" "$root/usr/share/doc/k7ctl" \
          "$root/usr/share/licenses/k7ctl" 2>/dev/null || true
fi
