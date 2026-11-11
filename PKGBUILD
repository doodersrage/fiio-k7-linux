# Builds the package from this source checkout:  makepkg -si
# (aur/PKGBUILD is the release version that downloads a tagged tarball.)
pkgname=k7ctl
pkgver=0.5.0
pkgrel=1
pkgdesc="FiiO K7 DAC/amp status and bit-perfect setup for Linux (PipeWire)"
arch=('any')
url="https://github.com/doodersrage/fiio-k7-linux"
license=('MIT')
depends=('python' 'pipewire' 'wireplumber')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')
optdepends=('python-pyqt6: system tray icon'
            'pyside6: system tray icon, alternative to PyQt6')

build() {
  cd "$startdir"
  rm -rf "$srcdir/dist"   # no stale wheels from earlier versions
  /usr/bin/python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  QT_QPA_PLATFORM=offscreen /usr/bin/python -m pytest -q -p no:cacheprovider
}

package() {
  /usr/bin/python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  "$startdir/install-files.sh" "$pkgdir"
}
