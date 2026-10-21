#!/usr/bin/env bash
# Usage: tools/decomp.sh <ClassName> [method] [overload-index]
# Decompile one APK method (default <clinit>) via rea; saves JSON to re/out/ and prints code.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${REA_JADX_MCP_JAR:?set REA_JADX_MCP_JAR to the jadx-headless-mcp 0.7.1 jar}"
cls="$1"; m="${2:-<clinit>}"; extra=()
[ -n "${3:-}" ] && extra=(--overload-index "$3")
out="$ROOT/re/out/${cls##*.}.${m//[<>]/_}.json"
cd "$ROOT"
npm exec --yes --package=rea-agents@6.7.0 -- rea inspect-android-method re/apk/base.apk "$cls" "$m" "${extra[@]}" --format json > "$out" 2>&1 || true
python3 -I - "$out" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
def find(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, str) and ("{" in v or "(" in v) and "\n" in v and len(v) > 40:
                yield k, v
            else:
                yield from find(v)
    elif isinstance(o, list):
        for v in o: yield from find(v)
hits = list(find(d))
if not hits:
    print(json.dumps(d)[:3000])
for k, v in hits[:2]:
    print(f"--- {k}\n{v}")
PY
