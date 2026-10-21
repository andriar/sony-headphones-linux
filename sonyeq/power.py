"""Battery status (POWER, inquired type BATTERY)."""
from dataclasses import dataclass

CMD_GET_STATUS = 0x22
CMD_RET_STATUS = 0x23
CMD_NTFY_STATUS = 0x25
TYPE_BATTERY = 0x00
CHARGING_NAMES = {0x00: "not charging", 0x01: "charging", 0x02: "unknown", 0x03: "charged"}


@dataclass(frozen=True)
class Battery:
    percent: int
    charging: str


def get_battery() -> bytes:
    return bytes((CMD_GET_STATUS, TYPE_BATTERY))


def parse_battery(payload: bytes) -> Battery:
    if len(payload) < 4 or payload[0] not in (CMD_RET_STATUS, CMD_NTFY_STATUS) or payload[1] != TYPE_BATTERY:
        raise ValueError(f"not a battery reply: {payload.hex(' ')}")
    return Battery(payload[2], CHARGING_NAMES.get(payload[3], f"0x{payload[3]:02x}"))
