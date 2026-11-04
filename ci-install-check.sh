#!/bin/sh
# Used by CI after installing a package: is everything where it should be?
set -ex
# where the command and the Python that has the k7ctl module should be
BIN=${EXPECT_BIN:-/usr/bin/k7ctl}
PY=${PYTHON:-/usr/bin/python3}
test "$(readlink -f "$(command -v k7ctl)")" = "$(readlink -f "$BIN")"
k7ctl --version
k7ctl --help >/dev/null
# no K7 in CI: must fail with the friendly message, not a traceback
out=$(k7ctl status 2>&1) && exit 1
echo "$out" | grep -q "No FiiO K7"
if echo "$out" | grep -q Traceback; then exit 1; fi
for f in \
    /usr/share/applications/k7ctl-tray.desktop \
    /usr/share/metainfo/io.github.doodersrage.k7ctl.metainfo.xml \
    /usr/share/bash-completion/completions/k7ctl \
    /usr/share/zsh/site-functions/_k7ctl \
    /usr/share/fish/vendor_completions.d/k7ctl.fish
do
    test -f "$f"
done
ls /usr/share/man/man1/k7ctl.1*
ls /usr/share/doc/k7ctl/
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate /usr/share/applications/k7ctl-tray.desktop
fi
# the tray module imports (offscreen) with the installed Qt binding
QT_QPA_PLATFORM=offscreen "$PY" -c "
from k7ctl import tray
app = tray.QApplication([])
print('tray ok:', tray.QApplication.__module__)"
