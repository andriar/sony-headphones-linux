#!/usr/bin/env python3
"""Write releases.json for the download page from the packages in dist/.

Usage: scripts/release-manifest.py <dist-dir> <base-url> [--notes "line" ...] > releases.json
<base-url> is where the version folder lives, e.g. https://dl.andriar.com/sony-headphones
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import sonyeq  # noqa: E402

FORMATS = {".deb": "deb", ".rpm": "rpm", ".flatpak": "flatpak"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dist", type=pathlib.Path)
    ap.add_argument("base_url")
    ap.add_argument("--notes", nargs="*", default=[])
    args = ap.parse_args()

    version = sonyeq.__version__
    files = []
    for path in sorted(args.dist.iterdir()):
        fmt = FORMATS.get(path.suffix)
        if not fmt or version not in path.name:
            continue
        files.append({
            "format": fmt,
            "name": path.name,
            "url": f"{args.base_url.rstrip('/')}/{version}/{path.name}",
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    if not files:
        sys.exit(f"no packages for version {version} in {args.dist}")
    manifest = {
        "product": "sony-headphones",
        "version": version,
        "released": datetime.date.today().isoformat(),
        "notes": args.notes,
        "files": files,
    }
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
