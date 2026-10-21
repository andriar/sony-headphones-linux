# sony-headphones-linux

Control Sony headphones from Linux without the phone app: noise cancelling,
ambient sound, the hardware equalizer, and battery level. Comes with:

- **Sony Headphones** (`sonyeq-app`), a GNOME app built with GTK 4 and libadwaita
- a **GNOME Shell Quick Settings toggle** for noise control, with the battery level
- a **tray icon** (`sonyeq-tray`) for desktops other than GNOME
- a **CLI** (`sonyeq`)

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
- For the app: PyGObject with cairo, GTK 4 and libadwaita 1.4+ (GNOME 45 or later).
  Ubuntu/Debian/Zorin: `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1`
- For the Quick Settings toggle: GNOME Shell 45 or later (tested on 46)
- For the tray icon: GTK 3 and Ayatana AppIndicator.
  Ubuntu/Debian/Zorin: `sudo apt install gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1`

The headset must be paired and connected in your Bluetooth settings first.
Close the Sony app on your phone while using this tool; the headset only takes
one control client at a time.

## Install

```bash
git clone https://github.com/andriar/sony-headphones-linux
cd sony-headphones-linux
pip install --user .        # or: pipx install --system-site-packages .
```

Without installing, run it from the checkout with `python3 -m sonyeq`,
`python3 -m sonyeq.app` and `python3 -m sonyeq.tray`.

Add the app to your app grid:

```bash
cp data/com.andriar.SonyHeadphones.desktop ~/.local/share/applications/
install -Dm644 packaging/flatpak/com.andriar.SonyHeadphones.svg \
  ~/.local/share/icons/hicolor/scalable/apps/com.andriar.SonyHeadphones.svg
```

On GNOME, add the Quick Settings toggle. It needs `sonyeq` installed (above).
On Wayland, log out and back in once so GNOME Shell sees the new extension, then enable it:

```bash
cp -r gnome-shell-extension/sony-headphones@andriar.github.io ~/.local/share/gnome-shell/extensions/
gnome-extensions enable sony-headphones@andriar.github.io
```

On other desktops, start the tray icon at login instead:

```bash
cp data/sonyeq-tray.desktop ~/.local/share/applications/
cp data/sonyeq-tray.desktop ~/.config/autostart/
```

The .deb and .rpm packages install all of this; you only enable the extension.

### Flatpak

`packaging/flatpak/` builds the app as a Flatpak (`flatpak run com.andriar.SonyHeadphones`).
It ships the app and the `sonyeq` CLI (`flatpak run --command=sonyeq com.andriar.SonyHeadphones status`),
but not the tray icon or the Quick Settings toggle; install those from the checkout or a package.

```bash
flatpak run org.flatpak.Builder --user --install --force-clean --install-deps-from=flathub \
  build/app packaging/flatpak/com.andriar.SonyHeadphones.yml
```

Two sandbox details:

- The app has network access (`--share=network`). It does not use the network, but Linux only
  creates Bluetooth sockets in the host's network namespace, and without it every RFCOMM
  connection fails with "Address family not supported by protocol".
- The headset lock (`$XDG_RUNTIME_DIR/sonyeq.lock`) is shared with `sonyeq` and the extension
  on the host through `--filesystem=xdg-run/sonyeq.lock`, but Flatpak can only share it when the
  file already exists when the app starts. Any host `sonyeq` command creates it, and it stays
  until you log out. If the app starts first, its lock is private to the sandbox, so a host
  command that talks to the headset at the same moment can fail to connect; run it again.

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
sonyeq --json status                        # machine-readable, for scripts (status and anc)
```

`bands` and `set` write to the `custom` preset, so the app's saved Custom 1 /
Custom 2 stay untouched unless you pass `--slot custom1` or `--slot custom2`.

## The app

Open **Sony Headphones** from the app grid, or run `sonyeq-app`. The header
shows the headset and its battery. Below it:

- **Noise Control**: Noise Canceling, Ambient Sound or Off. In ambient mode you
  also get the ambient level and Focus on Voice.
- **Equalizer**: the preset, and one slider per band that the headset reports.
  The line above the sliders previews the curve. Moving a slider saves the curve
  to the Custom preset, as the phone app does.

When no headset is paired, or it is off or out of range, the window says so and
offers the next step: open Bluetooth settings, or connect. It switches back to
the controls on its own when the headset connects. It follows the system's
light/dark style and accent color.

Shortcuts: <kbd>Ctrl</kbd>+<kbd>R</kbd> refresh, <kbd>Ctrl</kbd>+<kbd>W</kbd> close,
<kbd>Ctrl</kbd>+<kbd>?</kbd> all shortcuts.

## Quick Settings toggle (GNOME)

Adds **Noise Control** next to Power Mode in Quick Settings. Click it to turn
noise control off, or back on to the mode you used last. The subtitle shows the
mode and battery (`Canceling · 84%`); the arrow opens a menu with all three
modes and a link to the app. The toggle is hidden while the headset is off or
not paired, like the Bluetooth toggle without an adapter.

It runs `sonyeq --json status` and `sonyeq --json anc <mode>` instead of
talking to a D-Bus service. That is the simpler and sturdier choice here:

- Nothing has to run in the background, so there is no daemon to install,
  start at login, restart after a crash or keep in sync with the extension.
- The headset only takes one control client at a time and every call is short.
  The CLI takes a per-user lock (`$XDG_RUNTIME_DIR/sonyeq.lock`), so the
  extension, the app, the tray icon and your own `sonyeq` commands take turns
  instead of failing.
- The extension stays small JavaScript with no protocol code. All headset
  logic stays in Python, in one place.

The cost is one short process per refresh. The toggle refreshes when you open
Quick Settings, when the headset connects or disconnects, and every 5 minutes.

## Tray icon (other desktops)

For desktops without GNOME Shell. Shows the battery level in the panel; the
menu switches noise control and EQ presets, and "Open Sony Headphones…"
opens the app. When the headset disconnects the controls are greyed out; they
come back on their own when it reconnects. State is also re-read every 60
seconds, or on "Refresh". On GNOME, use the Quick Settings toggle instead.

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
