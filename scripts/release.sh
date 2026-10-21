#!/usr/bin/env bash
# Release the version in sonyeq/__init__.py from this machine, end to end:
#   tests → .deb/.rpm (nfpm) → .flatpak bundle → copy into apps-site → push apps-site
#   (its CI deploys apps.andriar.com) → tag vX.Y.Z → private GitHub release with the files.
# Usage: scripts/release.sh "One-line release note"     (SITE=path/to/apps-site to override)
# Needs: nfpm, org.flatpak.Builder (flatpak run), gh logged in, push access to both repos.
set -euo pipefail
cd "$(dirname "$0")/.."
NOTE="${1:?usage: scripts/release.sh \"release note\"}"
SITE="${SITE:-../apps-site}"
APP_ID=com.andriar.SonyHeadphones
VERSION="$(python3 -c 'import sonyeq; print(sonyeq.__version__)')"
TAG="v$VERSION"
step() { printf '\n== %s\n' "$*"; }

step "checks for $TAG"
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first" >&2; exit 1; }
[ -z "$(git -C "$SITE" status --porcelain)" ] || { echo "$SITE has uncommitted changes" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "HEAD is not origin/main; pull or push first" >&2; exit 1; }
! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "$TAG already exists; bump sonyeq.__version__" >&2; exit 1; }
git -C "$SITE" pull -q --ff-only

step "tests"
python3 -m unittest discover -s tests -t .

step ".deb and .rpm"
rm -f dist/*.deb dist/*.rpm dist/*.flatpak
scripts/build-packages.sh

step "flatpak bundle"
git submodule update --init -q
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from=flathub --repo=build/repo \
  build/app "packaging/flatpak/$APP_ID.yml" > build/flatpak.log 2>&1 || { tail -20 build/flatpak.log >&2; exit 1; }
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  build/repo "dist/sony-headphones-linux-$VERSION.flatpak" "$APP_ID"
ls -l dist/

step "publish to $SITE"
scripts/publish-site.sh "$SITE" "$NOTE"
git -C "$SITE" add site/sony-headphones
git -C "$SITE" commit -q -m "release: sony-headphones $TAG"
git -C "$SITE" push -q
echo "pushed; apps-site CI deploys https://apps.andriar.com/sony-headphones/"

step "tag and GitHub release"
git tag -a "$TAG" -m "$NOTE"
git push -q origin "$TAG"
gh release create "$TAG" dist/*.deb dist/*.rpm dist/*.flatpak "$SITE/site/sony-headphones/releases.json" \
  --title "$TAG" --notes "$NOTE"
step "done: $TAG"
