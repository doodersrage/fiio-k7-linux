#!/bin/sh
# Build the Debian/Ubuntu package with the debian/ packaging:  packaging/build-deb.sh [outdir]
# Needs: build-essential debhelper dh-python python3-pytest (dpkg-buildpackage runs the tests).
set -e
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src/dist}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/k7ctl/__init__.py")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# build from a clean copy (no .git or build leftovers: like a release tarball)
mkdir "$work/k7ctl-$version"
(cd "$src" && tar --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=pkg \
    --exclude=src --exclude='*.pkg.tar.zst' --exclude=dist --exclude=build --exclude='*.egg-info' \
    -cf - .) | tar -C "$work/k7ctl-$version" -xf -
# the package version follows k7ctl/__init__.py
sed -i "1s/^k7ctl ([^)]*)/k7ctl ($version)/" "$work/k7ctl-$version/debian/changelog"
(cd "$work/k7ctl-$version" && dpkg-buildpackage -us -uc -b)
mkdir -p "$out"
cp "$work"/k7ctl_*_all.deb "$out/"
