"""Send raw MDR v2 GET payloads to the connected Sony headset and print the replies.

Usage: python3 tools/probe.py 5600 6617 2200
Each argument is a hex payload. Only GET-style commands are allowed, so the probe
cannot change headset settings. Reply command = request command + 1.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sonyeq.bluez import control_channel, pick_device  # noqa: E402
from sonyeq.device import Link  # noqa: E402

GET_COMMANDS = {0x00, 0x20, 0x22, 0x26, 0x50, 0x52, 0x56, 0x5A, 0x60, 0x62, 0x66}

dev = pick_device()
with Link(dev.mac, control_channel(dev.mac)) as link:
    link.request(bytes((0x00, 0x00)), expect_cmd=0x01)
    for arg in sys.argv[1:]:
        payload = bytes.fromhex(arg)
        if payload[0] not in GET_COMMANDS:
            sys.exit(f"refusing non-GET command 0x{payload[0]:02x}")
        try:
            reply = link.request(payload, expect_cmd=payload[0] + 1, timeout=1.5)
            print(f"{arg:8} -> {reply.payload.hex(' ') if reply else 'ACK, no reply'}")
        except TimeoutError:
            print(f"{arg:8} -> no ACK")
