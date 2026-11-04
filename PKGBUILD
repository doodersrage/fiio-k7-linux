# Builds the package from this source checkout:  makepkg -si
pkgname=k7ctl
pkgver=0.1.0
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
  rm -rf "$srcdir/dist"
  /usr/bin/python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  QT_QPA_PLATFORM=offscreen /usr/bin/python -m pytest -q -p no:cacheprovider
}

package() {
  /usr/bin/python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  install -Dm644 "$startdir/data/k7ctl-tray.desktop" "$pkgdir/usr/share/applications/k7ctl-tray.desktop"
  install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
