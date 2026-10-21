"""Extract NAME -> byte value pairs from a decompiled smali enum <clinit>.

Usage: python3 -I tools/smali_enum.py re/out/<Class>._clinit_.json
Tracks const loads per register; at each `invoke-direct ... -><init>(Ljava/lang/String;I...)`
it reads the enum name (2nd arg) and the remaining args as values.
"""
import json
import re
import sys

CONST = re.compile(r"^\s*const(?:/4|/16|/high16|)?\s+(v\d+|p\d+),\s*(-?0x[0-9a-f]+|-?\d+)")
CONST_STR = re.compile(r'^\s*const-string(?:/jumbo)?\s+(v\d+|p\d+),\s*"(.*)"')
INIT = re.compile(r"^\s*invoke-direct(?:/range)?\s+\{([^}]*)\},\s*L[^;]+;-><init>\(Ljava/lang/String;I([^)]*)\)V")


def smali_text(path):
    d = json.load(open(path))
    stack = [d]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            for v in o.values():
                if isinstance(v, str) and ".method" in v:
                    return v
                stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    raise SystemExit(f"no smali in {path}")


def regs(spec):
    spec = spec.strip()
    if ".." in spec:
        a, b = [s.strip() for s in spec.split("..")]
        return [f"v{i}" for i in range(int(a[1:]), int(b[1:]) + 1)]
    return [r.strip() for r in spec.split(",") if r.strip()]


def main(path):
    vals = {}
    for line in smali_text(path).splitlines():
        if m := CONST_STR.match(line):
            vals[m.group(1)] = m.group(2)
        elif m := CONST.match(line):
            vals[m.group(1)] = int(m.group(2), 0)
        elif m := INIT.match(line):
            r = regs(m.group(1))
            name = vals.get(r[1])
            rest = [vals.get(x) for x in r[3:]]
            fmt = [f"0x{v & 0xff:02x}" if isinstance(v, int) else repr(v) for v in rest]
            print(f"{name:45} {' '.join(fmt)}")


if __name__ == "__main__":
    main(sys.argv[1])
