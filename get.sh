#!/bin/sh
# tether bootstrap: downloads a tether release, checks its checksum, and runs its installer.
#
#   curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/s4moore/tether/main/get.sh | sh -s -- --uninstall
#   TETHER_VERSION=0.1.0 sh get.sh        # a specific version instead of the latest
#
# Nothing is installed outside what install.sh does (see docs/install.md). To read everything first, download
# the release yourself (see "Install" in the README) and run ./install.sh from it.
set -eu

REPO="s4moore/tether"
VERSION="${TETHER_VERSION:-latest}"

say() { echo "==> $*"; }
die() { echo "error: $*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
command -v bash >/dev/null 2>&1 || die "bash is required (the installer is a bash script)"
if command -v shasum >/dev/null 2>&1; then SUM="shasum -a 256"
elif command -v sha256sum >/dev/null 2>&1; then SUM="sha256sum"
else die "shasum or sha256sum is required"; fi

if [ "$VERSION" = latest ]; then
  # the redirect target of /releases/latest is .../releases/tag/vX.Y.Z (no API call, no rate limit)
  url="$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")" || die "could not look up the latest release"
  VERSION="${url##*/v}"
  case "$VERSION" in [0-9]*.[0-9]*.[0-9]*) ;; *) die "could not read a version from $url (no release yet?)" ;; esac
fi
VERSION="${VERSION#v}"

base="${TETHER_BASE_URL:-https://github.com/$REPO/releases/download/v$VERSION}"   # TETHER_BASE_URL: for testing
tmp="$(mktemp -d "${TMPDIR:-/tmp}/tether-install.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT INT TERM

say "downloading tether $VERSION"
curl -fsSL -o "$tmp/tether-$VERSION.tar.gz" "$base/tether-$VERSION.tar.gz" || die "download failed: $base/tether-$VERSION.tar.gz"
curl -fsSL -o "$tmp/tether-$VERSION.tar.gz.sha256" "$base/tether-$VERSION.tar.gz.sha256" || die "download failed: the checksum file"

say "checking the checksum"
expected="$(cut -d' ' -f1 "$tmp/tether-$VERSION.tar.gz.sha256")"
actual="$($SUM "$tmp/tether-$VERSION.tar.gz" | cut -d' ' -f1)"
[ -n "$expected" ] && [ "$expected" = "$actual" ] || die "checksum mismatch (expected $expected, got $actual): nothing was installed"

tar -xzf "$tmp/tether-$VERSION.tar.gz" -C "$tmp"
src="$tmp/tether-$VERSION"
[ -f "$src/install.sh" ] || die "the download does not contain install.sh"
TETHER_SRC="$src" bash "$src/install.sh" "$@"
