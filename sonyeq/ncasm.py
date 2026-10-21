"""Noise cancelling / ambient sound payloads (NCASM, inquired type 0x17). See PROTOCOL.md."""
from dataclasses import dataclass

CMD_GET_CAPABILITY = 0x60
CMD_RET_CAPABILITY = 0x61
CMD_GET_PARAM = 0x66
CMD_RET_PARAM = 0x67
CMD_SET_PARAM = 0x68
CMD_NTFY_PARAM = 0x69
# Only this inquired type (verified on WH-1000XM5) is implemented; other types use other layouts.
TYPE_DUAL_NC_ASM_SEAMLESS = 0x17
CHANGED = 0x01

MIN_LEVEL, MAX_LEVEL = 1, 20
MODES = ("nc", "ambient", "off")


@dataclass(frozen=True)
class NcAsmState:
    enabled: bool
    ambient: bool  # False = noise cancelling
    voice: bool  # ambient "focus on voice"
    level: int  # ambient level 1..20

    @property
    def mode(self) -> str:
        if not self.enabled:
            return "off"
        return "ambient" if self.ambient else "nc"


def get_capability() -> bytes:
    return bytes((CMD_GET_CAPABILITY, TYPE_DUAL_NC_ASM_SEAMLESS))


def get_param() -> bytes:
    return bytes((CMD_GET_PARAM, TYPE_DUAL_NC_ASM_SEAMLESS))


def set_param(state: NcAsmState) -> bytes:
    if not MIN_LEVEL <= state.level <= MAX_LEVEL:
        raise ValueError(f"ambient level {state.level} out of range {MIN_LEVEL}..{MAX_LEVEL}")
    return bytes((CMD_SET_PARAM, TYPE_DUAL_NC_ASM_SEAMLESS, CHANGED,
                  int(state.enabled), int(state.ambient), int(state.voice), state.level))


def with_mode(state: NcAsmState, mode: str) -> NcAsmState:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == "off":
        return NcAsmState(False, state.ambient, state.voice, state.level)
    return NcAsmState(True, mode == "ambient", state.voice, state.level)


def parse_param(payload: bytes) -> NcAsmState:
    if (len(payload) < 7 or payload[0] not in (CMD_RET_PARAM, CMD_NTFY_PARAM)
            or payload[1] != TYPE_DUAL_NC_ASM_SEAMLESS):
        raise ValueError(f"not an NC/ASM param reply: {payload.hex(' ')}")
    return NcAsmState(bool(payload[3]), bool(payload[4]), bool(payload[5]), payload[6])
