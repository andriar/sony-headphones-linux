#!/usr/bin/env bash
# Build .deb and .rpm into dist/. Version comes from sonyeq/__init__.py unless VERSION is set.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${VERSION:-$(python3 -c 'import sonyeq; print(sonyeq.__version__)')}"
export VERSION
mkdir -p dist
find sonyeq -name '__pycache__' -prune -exec rm -rf {} +
for fmt in deb rpm; do
  nfpm package -f packaging/nfpm.yaml -p "$fmt" -t dist/
done
ls -l dist/
