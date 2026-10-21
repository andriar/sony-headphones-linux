"""Tray icon for Sony headphones (GTK3 + Ayatana AppIndicator), for desktops without the GNOME extension.

The menu switches noise control and EQ presets; everything else opens the libadwaita app (sonyeq.app).
"""
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import eq, ncasm  # noqa: E402
from .gui import MODE_LABELS, PRESET_LABELS, RECONNECT_DELAY_MS, BluezWatch, Worker, concerns  # noqa: E402
from .headset import Headset, HeadsetError, Snapshot  # noqa: E402

APP_ID = "sonyeq-tray"
ICON = "audio-headphones-symbolic"
POLL_SECONDS = 60


class App:
    def __init__(self):
        self.headset = Headset()
        self.worker = Worker(self.headset)
        self.snapshot: Snapshot | None = None
        self.connected = False
        self.syncing = False  # True while menu items are updated from headset state
        self._reconnect_timer = 0

        self.indicator = AppIndicator.Indicator.new(APP_ID, ICON, AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Sony headphones")
        self._build_menu()
        self.watch = BluezWatch(self._on_bluez_change)
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

    # ---- menu -------------------------------------------------------------
    def _build_menu(self):
        menu = Gtk.Menu()
        self.header = Gtk.MenuItem(label="Looking for headphones…")
        self.header.set_sensitive(False)
        menu.append(self.header)
        menu.append(Gtk.SeparatorMenuItem())

        self.mode_items: dict[str, Gtk.RadioMenuItem] = {}
        group = None
        for mode in ncasm.MODES:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, MODE_LABELS[mode])
            group = group or item
            item.connect("toggled", self._on_mode_item, mode)
            self.mode_items[mode] = item
            menu.append(item)
        self.mode_sep = Gtk.SeparatorMenuItem()
        menu.append(self.mode_sep)

        self.preset_root = Gtk.MenuItem(label="EQ preset")
        sub = Gtk.Menu()
        self.preset_items: dict[str, Gtk.RadioMenuItem] = {}
        group = None
        for name in eq.PRESETS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, PRESET_LABELS[name])
            group = group or item
            item.connect("toggled", self._on_preset_item, name)
            self.preset_items[name] = item
            sub.append(item)
        self.preset_root.set_submenu(sub)
        menu.append(self.preset_root)

        self.open_item = Gtk.MenuItem(label="Open Sony Headphones…")
        self.open_item.connect("activate", lambda _: self.open_app())
        menu.append(self.open_item)
        refresh_item = Gtk.MenuItem(label="Refresh")
        refresh_item.connect("activate", lambda _: self.refresh())
        menu.append(refresh_item)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)
        self._set_controls_sensitive(False)

    def _set_controls_sensitive(self, on: bool):
        for item in self.mode_items.values():
            item.set_sensitive(on)
        self.preset_root.set_sensitive(on)

    def _on_mode_item(self, item, mode):
        if item.get_active() and not self.syncing:
            self.set_mode(mode)

    def _on_preset_item(self, item, name):
        if item.get_active() and not self.syncing:
            self.set_preset(name)

    # ---- BlueZ connect/disconnect ------------------------------------------
    def _on_bluez_change(self, path, connected):
        dev = self.headset.device
        if not concerns(dev, path):
            return
        if connected:
            # Give the headset a moment to bring up its profiles before opening the control channel.
            if self._reconnect_timer:
                GLib.source_remove(self._reconnect_timer)
            self._reconnect_timer = GLib.timeout_add(RECONNECT_DELAY_MS, self._reconnect)
            self.header.set_label(f"{dev.name if dev else 'Headphones'} · connecting…")
        else:
            self._show_disconnected(f"{dev.name if dev else 'Headphones'} · disconnected")

    def _reconnect(self):
        self._reconnect_timer = 0
        self.refresh()
        return False

    # ---- headset actions ----------------------------------------------------
    def _poll(self):
        self.refresh()
        return True

    def refresh(self):
        self.worker.submit(lambda h: h.snapshot(), self._on_snapshot, self._on_error)

    def set_mode(self, mode):
        self.worker.submit(lambda h: h.set_ncasm(mode=mode), self._on_ncasm, self._on_error)

    def set_ambient(self, level=None, voice=None):
        self.worker.submit(lambda h: h.set_ncasm(level=level, voice=voice), self._on_ncasm, self._on_error)

    def set_preset(self, name):
        self.worker.submit(lambda h: h.set_preset(eq.PRESETS[name]), self._on_eq, self._on_error)

    def set_levels(self, levels):
        self.worker.submit(lambda h: h.set_levels(levels), self._on_eq, self._on_error)

    # ---- results ------------------------------------------------------------
    def _on_snapshot(self, snap: Snapshot):
        self.snapshot = snap
        self.connected = True
        self._sync()

    def _replace(self, **changes):
        if self.snapshot:
            fields = {f: getattr(self.snapshot, f) for f in ("name", "caps", "eq", "ncasm", "battery")}
            fields.update(changes)
            self.snapshot = Snapshot(**fields)

    def _on_ncasm(self, state):
        self._replace(ncasm=state)
        self._sync()

    def _on_eq(self, state):
        self._replace(eq=state)
        self._sync()

    def _on_error(self, err):
        if isinstance(err, HeadsetError) and err.disconnected:
            self._show_disconnected(str(err))
        elif self.connected:
            # A single failed command: re-read so the menu reflects what the headset really has.
            print(f"sonyeq-tray: {err}", file=sys.stderr)
            self.refresh()
        else:
            self._show_disconnected(str(err))

    def _show_disconnected(self, message: str):
        self.connected = False
        self.header.set_label(message if len(message) < 60 else message[:57] + "…")
        self.indicator.set_label("", "")
        self._set_controls_sensitive(False)

    def _sync(self):
        snap = self.snapshot
        if snap is None or not self.connected:
            return
        caps = snap.caps
        self.syncing = True
        try:
            battery = f" · {snap.battery.percent}%" if snap.battery else ""
            self.header.set_label(f"{snap.name}{battery}")
            self.indicator.set_label(f"{snap.battery.percent}%" if snap.battery else "", "100%")
            self.indicator.set_title(snap.name)
            self._set_controls_sensitive(True)

            for item in self.mode_items.values():
                item.set_visible(caps.ncasm)
            self.mode_sep.set_visible(caps.ncasm)
            if snap.ncasm:
                self.mode_items[snap.ncasm.mode].set_active(True)

            self.preset_root.set_visible(caps.eq is not None)
            if caps.eq:
                offered = set(caps.eq.preset_names)
                for name, item in self.preset_items.items():
                    item.set_visible(name in offered)
            if snap.eq:
                name = eq.PRESET_NAMES.get(snap.eq.preset)
                if name in self.preset_items:
                    self.preset_items[name].set_active(True)
                self.preset_root.set_label(f"EQ preset: {PRESET_LABELS.get(name, '?')}")
        finally:
            self.syncing = False

    def open_app(self):
        # The libadwaita app is a single-instance GApplication: this presents it if it already runs.
        try:
            Gio.Subprocess.new([sys.executable, "-m", "sonyeq.app"], Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.header.set_label(f"Cannot open the app: {e.message}")


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    App()
    Gtk.main()


if __name__ == "__main__":
    main()
