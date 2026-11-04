#!/bin/sh
# Build an RPM from this source tree:  packaging/build-rpm.sh [--srpm] [outdir]   (needs rpm-build)
# --srpm builds only the source RPM (what Fedora COPR does via .copr/Makefile).
set -e
srpm=
if [ "$1" = --srpm ]; then srpm=1; shift; fi
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/k7ctl/__init__.py")
# the directory name GitHub's tag tarballs use (see Source0 in the spec)
name=fiio-k7-linux-$version
top=$(mktemp -d)
trap 'rm -rf "$top"' EXIT
mkdir -p "$top/SOURCES" "$top/staging/$name"
# the same files a release tarball has (no .git or build leftovers)
(cd "$src" && tar --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=pkg \
    --exclude=src --exclude='*.pkg.tar.zst' --exclude=dist --exclude=build --exclude='*.egg-info' \
    -cf - .) | tar -C "$top/staging/$name" -xf -
tar -C "$top/staging" -czf "$top/SOURCES/$name.tar.gz" "$name"
sed "s/^Version:.*/Version:        $version/" "$src/packaging/k7ctl.spec" > "$top/k7ctl.spec"
mkdir -p "$out"
if [ -n "$srpm" ]; then
    rpmbuild -bs --define "_topdir $top" "$top/k7ctl.spec"
    cp "$top"/SRPMS/*.src.rpm "$out/"
else
    rpmbuild -bb --define "_topdir $top" "$top/k7ctl.spec"
    cp "$top"/RPMS/noarch/*.rpm "$out/"
fi
