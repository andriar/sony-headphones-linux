"""sonyeq — control Sony headphones (MDR protocol v2) from Linux: EQ, noise control, battery."""
import argparse
import json
import sys

from . import eq, ncasm
from .headset import Headset, HeadsetError


def show_eq(caps: eq.EqCaps, state: eq.EqState) -> None:
    print(f"preset: {state.preset_name}")
    for name, db in zip(caps.slot_names, state.levels):
        print(f"  {name:>10} {db:+3d}")


def show_ncasm(state: ncasm.NcAsmState) -> None:
    print(f"noise control: {state.mode}  (ambient level {state.level}, "
          f"focus on voice {'on' if state.voice else 'off'})")


def ncasm_json(state: ncasm.NcAsmState) -> dict:
    return {"mode": state.mode, "level": state.level, "voice": state.voice}


def snapshot_json(snap) -> dict:
    return {
        "name": snap.name,
        "battery": {"percent": snap.battery.percent, "charging": snap.battery.charging} if snap.battery else None,
        "ncasm": ncasm_json(snap.ncasm) if snap.ncasm else None,
        "eq": {"preset": snap.eq.preset_name, "levels": dict(zip(snap.caps.eq.slot_names, snap.eq.levels))}
        if snap.eq else None,
    }


def parse_assignments(pairs: list[str]) -> dict[str, int]:
    out = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"bad assignment '{pair}'; use <slot>=<dB>, e.g. clear-bass=5")
        out[name] = int(value)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sonyeq", description=__doc__)
    ap.add_argument("--mac", help="headset address (default: $SONYEQ_MAC or the paired Sony headset)")
    ap.add_argument("--channel", type=int, help="RFCOMM channel (default: looked up via SDP)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw frames")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output for status and anc; errors become {\"error\": …}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show battery, noise control and EQ")
    sub.add_parser("info", help="show what this headset supports")
    p = sub.add_parser("preset", help="switch EQ preset")
    p.add_argument("name", choices=list(eq.PRESETS))
    b = sub.add_parser("bands", help="write all levels into a custom slot and select it")
    b.add_argument("levels", nargs="+", type=int, metavar="dB", help="one value per slot, see `sonyeq info`")
    b.add_argument("--slot", choices=list(eq.CUSTOM_SLOTS), default="custom")
    s = sub.add_parser("set", help="change some levels, keep the rest (e.g. set clear-bass=5 16k=-2)")
    s.add_argument("assignments", nargs="+", metavar="slot=dB")
    s.add_argument("--slot", choices=list(eq.CUSTOM_SLOTS), default="custom")
    a = sub.add_parser("anc", help="noise control: nc | ambient | off, optional ambient level/voice")
    a.add_argument("mode", nargs="?", choices=ncasm.MODES)
    a.add_argument("--level", type=int, help=f"ambient level {ncasm.MIN_LEVEL}..{ncasm.MAX_LEVEL}")
    a.add_argument("--voice", choices=("on", "off"), help="ambient focus on voice")
    sub.add_parser("battery", help="show battery level")
    args = ap.parse_args(argv)

    hs = Headset(args.mac, args.channel, verbose=args.verbose)
    try:
        if args.json and args.cmd not in ("status", "anc"):
            raise ValueError("--json is supported for status and anc")
        if args.cmd == "status" and args.json:
            print(json.dumps(snapshot_json(hs.snapshot())))
        elif args.cmd == "status":
            snap = hs.snapshot()
            print(snap.name)
            if snap.battery:
                print(f"battery: {snap.battery.percent}% ({snap.battery.charging})")
            if snap.ncasm:
                show_ncasm(snap.ncasm)
            if snap.eq:
                show_eq(snap.caps.eq, snap.eq)
        elif args.cmd == "info":
            caps = hs.caps()
            print(hs.device.name)
            if caps.eq:
                print(f"equalizer: {caps.eq.bands} slots ({', '.join(caps.eq.slot_names)}), "
                      f"{caps.eq.min_db}..{caps.eq.max_db} dB")
                print(f"  presets: {', '.join(caps.eq.preset_names)}")
            else:
                print("equalizer: not supported")
            print(f"noise control: {'supported' if caps.ncasm else 'not supported yet'}")
            print(f"battery: {'supported' if caps.battery else 'not supported yet'}")
        elif args.cmd == "preset":
            state = hs.set_preset(eq.PRESETS[args.name])
            show_eq(hs.caps().eq, state)
        elif args.cmd == "bands":
            state = hs.set_levels(args.levels, eq.CUSTOM_SLOTS[args.slot])
            show_eq(hs.caps().eq, state)
        elif args.cmd == "set":
            state = hs.update_levels(parse_assignments(args.assignments), eq.CUSTOM_SLOTS[args.slot])
            show_eq(hs.caps().eq, state)
        elif args.cmd == "anc":
            voice = None if args.voice is None else args.voice == "on"
            if args.mode is None and args.level is None and voice is None:
                state = hs.read_ncasm()
            else:
                state = hs.set_ncasm(args.mode, args.level, voice)
            if args.json:
                print(json.dumps(ncasm_json(state)))
            else:
                show_ncasm(state)
        elif args.cmd == "battery":
            bat = hs.read_battery()
            print(f"{bat.percent}% ({bat.charging})")
    except (ValueError, HeadsetError) as e:
        if args.json:
            print(json.dumps({"error": str(e), "disconnected": getattr(e, "disconnected", False)}))
            sys.exit(1)
        sys.exit(f"sonyeq: {e}")
