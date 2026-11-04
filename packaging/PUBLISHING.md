# Publishing k7ctl

Every release (bump `k7ctl/__init__.py`, add a `## X.Y.Z` section to `CHANGELOG.md`, push a tag `vX.Y.Z`) puts
`.deb`, `.rpm` and Arch packages on the GitHub release page automatically (`.github/workflows/release.yml`).
The channels below give users automatic updates through their package manager; each needs an account once.
CI checks on every push that the packaging for all of them still builds.

Before tagging, keep these in step with the new version: `debian/changelog` (the deb build rewrites the
version, but the entry text is yours), the `%changelog` in `packaging/k7ctl.spec`, `pkgver` in `PKGBUILD`
and `aur/PKGBUILD`, and a `<release>` in `data/io.github.doodersrage.k7ctl.metainfo.xml`.

## Fedora: COPR

1. Sign in at <https://copr.fedorainfracloud.org> with a Fedora account (free).
2. *New project* `k7ctl`, chroots: the current Fedora releases (e.g. `fedora-*-x86_64`; the package is `noarch`).
3. *Packages → New package → SCM*:
   - Clone URL: `https://github.com/doodersrage/fiio-k7-linux`
   - Committish: the release tag, e.g. `v0.2.0` (or leave empty for the latest code)
   - Build SRPM with: **make srpm** (uses `.copr/Makefile`)
4. *Rebuild*. Optionally add a GitHub webhook (COPR → Settings → Integrations) to build every tag.

Users then install with:

```sh
sudo dnf copr enable doodersrage/k7ctl
sudo dnf install k7ctl
```

## Ubuntu: Launchpad PPA

One-time setup:
1. Create a Launchpad account, upload your GPG key and sign the Ubuntu Code of Conduct.
2. Create a PPA named `k7ctl` (<https://launchpad.net/~YOUR-ID/+activate-ppa>).
3. `sudo apt install devscripts debhelper dh-python dput`

For each release and Ubuntu series (Launchpad needs one upload per series, e.g. `jammy`, `noble`):

```sh
git checkout v0.2.0
dch -v "0.2.0~noble1" -D noble "New upstream release."   # edits debian/changelog (don't commit this)
debuild -S -sa                                          # signed source package in ../
dput ppa:YOUR-ID/k7ctl ../k7ctl_0.2.0~noble1_source.changes
git checkout debian/changelog
```

Users then install with:

```sh
sudo add-apt-repository ppa:YOUR-ID/k7ctl
sudo apt install k7ctl
```

## Arch: AUR

1. Create an AUR account and add your SSH public key.
2. After tagging a release: in `aur/`, set `pkgver`, run `updpkgsums` and `makepkg --printsrcinfo > .SRCINFO`.
3. `git clone ssh://aur@aur.archlinux.org/k7ctl.git`, copy `PKGBUILD` and `.SRCINFO` into it, commit and push.
