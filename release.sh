#!/usr/bin/env bash
# Maintainers: build the release files for the version in VERSION from the committed tree.
#   ./release.sh        -> dist/tether-<version>.tar.gz and dist/tether-<version>.tar.gz.sha256
# Then:  git tag v<version> && git push origin v<version>   (the release workflow publishes them), or by hand:
#   gh release create v<version> dist/* get.sh --title "tether <version>" --notes "..."
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
V="$(cat VERSION)"
grep -q "^__version__ = \"$V\"" bin/tether || { echo "VERSION ($V) and bin/tether __version__ disagree" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "the working tree has uncommitted changes: commit first" >&2; exit 1; }
mkdir -p dist
git archive --format=tar.gz --prefix="tether-$V/" -o "dist/tether-$V.tar.gz" HEAD
(cd dist && { shasum -a 256 "tether-$V.tar.gz" 2>/dev/null || sha256sum "tether-$V.tar.gz"; } > "tether-$V.tar.gz.sha256")
cat "dist/tether-$V.tar.gz.sha256"
