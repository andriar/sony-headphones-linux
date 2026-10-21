# sony-headphones-linux

Control Sony headphones from Linux without the phone app: noise cancelling,
ambient sound, the hardware equalizer, and battery level. Comes with a CLI
(`sonyeq`) and a tray icon (`sonyeq-tray`).

Settings are written to the headset itself, so they stay in effect on your
phone, laptop and any other device.

Unofficial project, not affiliated with or endorsed by Sony.

## Supported devices

| Device | Status |
|--------|--------|
| WH-1000XM5 | Tested: EQ, noise cancelling / ambient, battery |
| Other Sony models that use protocol v2 (e.g. WH-1000XM6, WF-1000XM5) | Should be detected; untested. Features the headset does not report are hidden. |
| Older models on Sony's v1 control service (e.g. WH-1000XM3/XM4) | Not supported yet. The tool says so instead of sending commands. |

The tool picks the paired headset that advertises Sony's v2 control service
(UUID `956c7b26-d49a-4ba8-b03f-b17d393cb6e2`) and asks it what it supports
before showing any controls. Noise control is only enabled for the layout
verified on the WH-1000XM5. Reports from other models are welcome; run
`sonyeq info` and `python3 tools/probe.py 5000 6017 6617 2200`, which only reads.

## Requirements

- Linux with BlueZ (`bluetoothctl`; `sdptool` optional, used to find the RFCOMM channel)
- Python 3.10+
- For the tray icon: PyGObject, GTK 3 and Ayatana AppIndicator
  (on GNOME also the AppIndicator extension). Ubuntu/Debian/Zorin:
  `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1`

The headset must be paired and connected in your Bluetooth settings first.
Close the Sony app on your phone while using this tool; the headset only takes
one control client at a time.

## Install

```bash
git clone https://github.com/andriar/sony-headphones-linux
cd sony-headphones-linux
pip install --user .        # or: pipx install --system-site-packages .
```

Without installing, run it from the checkout with `python3 -m sonyeq` and
`python3 -m sonyeq.tray`.

Tray icon at login:

```bash
cp data/sonyeq-tray.desktop ~/.local/share/applications/
cp data/sonyeq-tray.desktop ~/.config/autostart/
```

## CLI

```bash
sonyeq status                               # battery, noise control, EQ
sonyeq info                                 # what this headset supports
sonyeq anc nc                               # noise cancelling
sonyeq anc ambient --level 15 --voice on    # ambient sound, level 1..20, focus on voice
sonyeq anc off
sonyeq preset bright                        # off bright excited mellow relaxed vocal treble bass speech custom custom1 custom2
sonyeq bands 3 0 0 1 2 1                    # WH-1000XM5: clear-bass 400 1k 2.5k 6.3k 16k, each -10..10
sonyeq set clear-bass=5 16k=-2              # change some levels, keep the rest
sonyeq battery
```

`bands` and `set` write to the `custom` preset, so the app's saved Custom 1 /
Custom 2 stay untouched unless you pass `--slot custom1` or `--slot custom2`.

## Tray icon

Shows the battery level in the top bar. The menu switches noise control and EQ
presets; "Equalizer & ambient…" opens a window with the band sliders, ambient
level and focus on voice. When the headset disconnects the controls are
greyed out; they come back on their own when it reconnects. State is also
re-read every 60 seconds, or on "Refresh".

## How it works

Sony headphones expose a serial (RFCOMM) control channel that the phone app
talks to. The frame format and command IDs were worked out for
interoperability from the Android app's protocol tables and confirmed against
a real WH-1000XM5; see [PROTOCOL.md](PROTOCOL.md). No Sony code is included.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

`tools/decomp.sh` and `tools/smali_enum.py` are the helpers used to read enum
tables from the app with [rea](https://github.com/morluto/rea); they expect
your own copy of the APK under `re/apk/`, which is git-ignored.

## License

MIT
