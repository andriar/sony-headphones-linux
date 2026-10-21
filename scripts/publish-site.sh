#!/usr/bin/env bash
# Copy the built packages into the apps-site checkout and rewrite releases.json there.
# Usage: scripts/publish-site.sh [path/to/apps-site] [release note]
# Then deploy the site (see apps-site/README.md). Run scripts/build-packages.sh first.
set -euo pipefail
cd "$(dirname "$0")/.."
SITE="${1:-../apps-site}"
NOTE="${2:-}"
VERSION="$(python3 -c 'import sonyeq; print(sonyeq.__version__)')"
DEST="$SITE/site/sony-headphones/dl/$VERSION"
shopt -s nullglob
files=(dist/*"$VERSION"*.deb dist/*"$VERSION"*.rpm)
[ ${#files[@]} -gt 0 ] || { echo "no packages for $VERSION in dist/; run scripts/build-packages.sh" >&2; exit 1; }
mkdir -p "$DEST"
cp "${files[@]}" "$DEST/"
scripts/release-manifest.py "$DEST" "dl" ${NOTE:+--notes "$NOTE"} \
  > "$SITE/site/sony-headphones/releases.json"
echo "copied ${#files[@]} package(s) to $DEST and wrote releases.json for $VERSION"
