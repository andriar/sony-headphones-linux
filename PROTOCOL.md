# Sony MDR v2 — EQ, noise control, battery (WH-1000XM5)

Source: own static analysis of Sound Connect 13.3.0 (versionCode 130305109)
plus live read/write probes against my own WH-1000XM5 from Linux
(`tools/probe.py`; raw logs kept local).
Rows marked **verified** were confirmed against the headset.

## Transport — verified
- Bluetooth Classic RFCOMM, SDP service "Serial HPC",
  UUID `956c7b26-d49a-4ba8-b03f-b17d393cb6e2`, channel 9 on this unit.
- Only one control client at a time: close Sound Connect on the phone
  (or let it disconnect) before using the CLI.

## Frame — verified
```
3e | escape( type | seq | len:u32 big-endian | payload | checksum ) | 3c
checksum = (type + seq + len bytes + payload bytes) & 0xff
escape: 3e -> 3d 2e, 3c -> 3d 2c, 3d -> 3d 2d  (decode: 3d XX -> XX | 0x10)
```
- Type: `0x0c` data (needs ACK), `0x0e` data table 2, `0x01` ACK.
- ACK for a frame with seq `s` carries seq `1 - s` and an empty payload.
- The sender's next seq is the seq carried by the ACK it just received.
- The headset ACKs every data frame and sends its own replies/notifications
  as data frames, which the client must ACK.

## Handshake — verified
`00 00` (CONNECT_GET_PROTOCOL_INFO) → `01 00 03 00 20 16 00 00`.

## EQ commands (table 1)
| ID | Name | Status |
|----|------|--------|
| 0x50 / 0x51 | get / ret capability | verified |
| 0x56 / 0x57 | get / ret param | verified |
| 0x58 | set param | verified (no reply, headset ACKs then notifies) |
| 0x59 | notify param | verified |
| 0x52 / 0x53 / 0x55 | status get / ret / notify | from APK only |

Second byte is the inquired type. XM5 answers only `0x00` (PRESET_EQ);
`0x01` (EBB) gets an ACK but no reply — Clear Bass lives inside the
PRESET_EQ band list instead.

## Payloads — verified
- Capability: `51 00 <bands=06> <steps=0x15> <presets=0x0c> (<presetId> 00)×n`
  - 6 slots, 21 steps → values 0..20 encode −10..+10 dB (offset 10).
  - Presets on this unit: 00 10 11 12 13 14 15 16 17 a0 a1 a2.
- Get: `56 00` → `57 00 <presetId> 06 <v1..v6>`
- Set preset: `58 00 <presetId> 00` (band count 0 → device fills the curve).
- Set custom: `58 00 a0 06 <v1..v6>`.
- Notify after set: `59 00 <presetId> 06 <v1..v6>`.

Example — Bright (`0x10`) reads back as `09 0a 0f 11 11 13`.

## Slot order — verified
`[Clear Bass, 400 Hz, 1 kHz, 2.5 kHz, 6.3 kHz, 16 kHz]`.
Proof: the app's Custom 1 showed Clear Bass +3 and 400 Hz −5; selecting
`a1` from Linux read back `0d 05 0a 0a 0a 0a`.

`a0` (custom) is a scratch slot; `a1` / `a2` are the app's Custom 1 / Custom 2.

## Preset IDs (from APK enum)
| ID | Preset | ID | Preset |
|----|--------|----|--------|
| 0x00 | Off | 0x14 | Vocal |
| 0x10 | Bright | 0x15 | Treble boost |
| 0x11 | Excited | 0x16 | Bass boost |
| 0x12 | Mellow | 0x17 | Speech |
| 0x13 | Relaxed | 0xa0 | Custom |
| | | 0xa1 / 0xa2 | User setting 1 / 2 |

## Noise cancelling / ambient (NCASM) — verified
Commands: 0x60/0x61 capability, 0x66/0x67 get/ret param, 0x68 set, 0x69 notify.
XM5 answers inquired type `0x17` (dual NC, mode switch, seamless ambient).

`<cmd> 17 <changed=01> <enabled> <mode: 00 NC / 01 ambient> <focus-on-voice> <level 1..20>`

- Get `66 17` → e.g. `67 17 01 01 00 00 0c` = NC on, ambient level 12, voice off.
- Set `68 17 01 01 01 01 0a` = ambient, focus on voice, level 10.
- Set `68 17 01 00 ...` = off. In NC or off mode the headset ignores the
  voice/level bytes and keeps its stored ambient settings.
- Capability `60 17` → `61 17 02 00 01 14 01 01 01 14 01` (level max 0x14 = 20).

## Battery — verified
`22 00` (POWER_GET_STATUS, BATTERY) → `23 00 <percent> <charging>`;
charging `00` not charging, `01` charging, `03` charged. Example `23 00 57 00` = 87 %.
