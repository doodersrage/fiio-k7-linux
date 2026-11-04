# Fedora / RPM spec. Build from a source checkout with packaging/build-rpm.sh,
# or use it on COPR (Source0 points at the tagged release).
%global srcname fiio-k7-linux

Name:           k7ctl
Version:        0.2.0
Release:        1%{?dist}
Summary:        FiiO K7 DAC/amp status and bit-perfect setup for PipeWire
License:        MIT
URL:            https://github.com/doodersrage/fiio-k7-linux
Source0:        %{url}/archive/refs/tags/v%{version}/%{srcname}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-pytest
Requires:       python3
Requires:       pipewire-utils
Requires:       wireplumber
Recommends:     python3-pyqt6

%description
Shows what the FiiO K7 USB DAC/headphone amp is playing (sample rate, bit
depth, PCM or DSD) and its firmware version, lets PipeWire switch the K7 to the
native sample rate of the music instead of resampling everything to 48 kHz, and
checks that PipeWire's volume isn't throwing away resolution. Includes a command
line tool with JSON output and a system tray icon (needs PyQt6 or PySide6).

%prep
%autosetup -n %{srcname}-%{version}

%build

%install
mkdir -p %{buildroot}%{python3_sitelib} %{buildroot}%{_bindir}
cp -r k7ctl %{buildroot}%{python3_sitelib}/
find %{buildroot}%{python3_sitelib} -name __pycache__ -prune -exec rm -rf {} +
cat > %{buildroot}%{_bindir}/k7ctl <<'PY'
#!/usr/bin/python3
import sys
from k7ctl.cli import main
sys.exit(main())
PY
chmod 755 %{buildroot}%{_bindir}/k7ctl
./install-files.sh %{buildroot}
# rpm puts these itself
rm -rf %{buildroot}%{_datadir}/licenses %{buildroot}%{_docdir}

%check
QT_QPA_PLATFORM=offscreen %{python3} -m pytest -q -p no:cacheprovider tests

%files
%license LICENSE
%doc README.md contrib/polybar.ini contrib/waybar
%{_bindir}/k7ctl
%{_mandir}/man1/k7ctl.1*
%{python3_sitelib}/k7ctl/
%{_datadir}/applications/k7ctl-tray.desktop
%{_metainfodir}/io.github.doodersrage.k7ctl.metainfo.xml
%{_datadir}/bash-completion/completions/k7ctl
%{_datadir}/zsh/site-functions/_k7ctl
%{_datadir}/fish/vendor_completions.d/k7ctl.fish

%changelog
* Wed Nov 04 2026 Robert McDowell <doodersrage@gmail.com> - 0.2.0-1
- Show which app keeps the K7 at another rate and which streams are resampled
- keepawake, latency presets, headphone EQ (AutoEQ), status bar output
- Shell completions and man page

* Wed Nov 04 2026 Robert McDowell <doodersrage@gmail.com> - 0.1.0-1
- Initial release
