"""EQ payloads for Sony MDR v2 (table 1, inquired type PRESET_EQ). See PROTOCOL.md."""
from dataclasses import dataclass

CMD_GET_CAPABILITY = 0x50
CMD_RET_CAPABILITY = 0x51
CMD_GET_PARAM = 0x56
CMD_RET_PARAM = 0x57
CMD_SET_PARAM = 0x58
CMD_NTFY_PARAM = 0x59
TYPE_PRESET_EQ = 0x00

PRESETS = {
    "off": 0x00,
    "bright": 0x10,
    "excited": 0x11,
    "mellow": 0x12,
    "relaxed": 0x13,
    "vocal": 0x14,
    "treble": 0x15,
    "bass": 0x16,
    "speech": 0x17,
    "custom": 0xA0,
    "custom1": 0xA1,
    "custom2": 0xA2,
}
PRESET_NAMES = {v: k for k, v in PRESETS.items()}
CUSTOM_SLOTS = {"custom": 0xA0, "custom1": 0xA1, "custom2": 0xA2}

FIVE_BANDS = ("400", "1k", "2.5k", "6.3k", "16k")
XM5_CAPS_RAW = "51 00 06 15 0c 00 00 10 00 11 00 12 00 13 00 14 00 15 00 16 00 17 00 a0 00 a1 00 a2 00"


@dataclass(frozen=True)
class EqCaps:
    bands: int
    steps: int
    presets: tuple[int, ...]

    @property
    def offset(self) -> int:
        return self.steps // 2

    @property
    def min_db(self) -> int:
        return -self.offset

    @property
    def max_db(self) -> int:
        return self.steps - 1 - self.offset

    @property
    def slot_names(self) -> tuple[str, ...]:
        # 6 slots verified on WH-1000XM5 as Clear Bass + 5 bands; other layouts are labelled generically.
        if self.bands == 6:
            return ("clear-bass", *FIVE_BANDS)
        if self.bands == 5:
            return FIVE_BANDS
        return tuple(f"band{i + 1}" for i in range(self.bands))

    @property
    def preset_names(self) -> list[str]:
        return [PRESET_NAMES[p] for p in self.presets if p in PRESET_NAMES]

    @property
    def custom_slots(self) -> dict[str, int]:
        return {k: v for k, v in CUSTOM_SLOTS.items() if v in self.presets}


@dataclass(frozen=True)
class EqState:
    preset: int
    levels: tuple[int, ...]  # dB, ordered as EqCaps.slot_names

    @property
    def preset_name(self) -> str:
        return PRESET_NAMES.get(self.preset, f"0x{self.preset:02x}")


def get_param() -> bytes:
    return bytes((CMD_GET_PARAM, TYPE_PRESET_EQ))


def get_capability() -> bytes:
    return bytes((CMD_GET_CAPABILITY, TYPE_PRESET_EQ))


def set_preset(preset_id: int) -> bytes:
    return bytes((CMD_SET_PARAM, TYPE_PRESET_EQ, preset_id, 0))


def set_levels(caps: EqCaps, levels, slot: int = CUSTOM_SLOTS["custom"]) -> bytes:
    levels = list(levels)
    if len(levels) != caps.bands:
        raise ValueError(f"need {caps.bands} values ({', '.join(caps.slot_names)}), got {len(levels)}")
    for name, db in zip(caps.slot_names, levels):
        if not caps.min_db <= db <= caps.max_db:
            raise ValueError(f"{name} = {db} out of range {caps.min_db}..{caps.max_db}")
    if slot not in caps.custom_slots.values():
        raise ValueError(f"slot 0x{slot:02x} is not a custom preset on this headset")
    return bytes((CMD_SET_PARAM, TYPE_PRESET_EQ, slot, len(levels), *(db + caps.offset for db in levels)))


def parse_param(caps: EqCaps, payload: bytes) -> EqState:
    if len(payload) < 4 or payload[0] not in (CMD_RET_PARAM, CMD_NTFY_PARAM) or payload[1] != TYPE_PRESET_EQ:
        raise ValueError(f"not an EQ param reply: {payload.hex(' ')}")
    count = payload[3]
    raw = payload[4 : 4 + count]
    if len(raw) != count:
        raise ValueError(f"truncated EQ reply: {payload.hex(' ')}")
    return EqState(payload[2], tuple(v - caps.offset for v in raw))


def parse_capability(payload: bytes) -> EqCaps:
    if len(payload) < 5 or payload[0] != CMD_RET_CAPABILITY or payload[1] != TYPE_PRESET_EQ:
        raise ValueError(f"not an EQ capability reply: {payload.hex(' ')}")
    bands, steps, n = payload[2], payload[3], payload[4]
    return EqCaps(bands, steps, tuple(payload[5 : 5 + 2 * n : 2]))
