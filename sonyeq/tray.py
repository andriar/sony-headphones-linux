"""Tray icon + window for Sony headphones (GTK3 + Ayatana AppIndicator)."""
import queue
import signal
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import eq, ncasm  # noqa: E402
from .headset import Headset, HeadsetError, Snapshot  # noqa: E402

APP_ID = "sonyeq-tray"
ICON = "audio-headphones-symbolic"
POLL_SECONDS = 60
RECONNECT_DELAY_MS = 3000
SLIDER_DEBOUNCE_MS = 350
MODE_LABELS = {"nc": "Noise cancelling", "ambient": "Ambient sound", "off": "Noise control off"}
PRESET_LABELS = {
    "off": "Off", "bright": "Bright", "excited": "Excited", "mellow": "Mellow", "relaxed": "Relaxed",
    "vocal": "Vocal", "treble": "Treble boost", "bass": "Bass boost", "speech": "Speech",
    "custom": "Custom", "custom1": "Custom 1", "custom2": "Custom 2",
}


def slot_label(name: str) -> str:
    return "Clear\nBass" if name == "clear-bass" else name


class Worker:
    """Runs headset calls one at a time off the GTK thread; delivers results with GLib.idle_add."""

    def __init__(self, headset: Headset):
        self.headset = headset
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, on_done, on_error):
        self.jobs.put((fn, on_done, on_error))

    def _run(self):
        while True:
            fn, on_done, on_error = self.jobs.get()
            try:
                result = fn(self.headset)
            except (HeadsetError, ValueError) as e:
                GLib.idle_add(on_error, e)
            else:
                GLib.idle_add(on_done, result)


class App:
    def __init__(self):
        self.headset = Headset()
        self.worker = Worker(self.headset)
        self.snapshot: Snapshot | None = None
        self.connected = False
        self.syncing = False  # True while widgets are updated from headset state
        self.window: HeadsetWindow | None = None
        self._reconnect_timer = 0

        self.indicator = AppIndicator.Indicator.new(APP_ID, ICON, AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Sony headphones")
        self._build_menu()
        self._watch_bluez()
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

        self.open_item = Gtk.MenuItem(label="Equalizer & ambient…")
        self.open_item.connect("activate", lambda _: self.open_window())
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
    def _watch_bluez(self):
        try:
            # Keep a reference: the subscription is dropped when the connection object is collected.
            self._system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        except GLib.Error:
            return  # no system bus: rely on polling
        self._system_bus.signal_subscribe("org.bluez", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                          None, "org.bluez.Device1", Gio.DBusSignalFlags.NONE,
                                          self._on_bluez_change)

    def _on_bluez_change(self, _conn, _sender, path, _iface, _signal, params):
        _, changed, _ = params.unpack()
        if "Connected" not in changed:
            return
        dev = self.headset.device
        if dev and not path.endswith(dev.dbus_path_suffix):
            return
        if changed["Connected"]:
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
            # A single failed command: re-read so the UI reflects what the headset really has.
            if self.window:
                self.window.show_message(str(err))
            self.refresh()
        else:
            self._show_disconnected(str(err))

    def _show_disconnected(self, message: str):
        self.connected = False
        self.header.set_label(message if len(message) < 60 else message[:57] + "…")
        self.indicator.set_label("", "")
        self._set_controls_sensitive(False)
        if self.window:
            self.window.set_disconnected(message)

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
            if self.window:
                self.window.sync(snap)
        finally:
            self.syncing = False

    def open_window(self):
        if self.window is None:
            self.window = HeadsetWindow(self)
            self.window.connect("destroy", self._on_window_closed)
        self.window.show_all()
        self.window.present()
        if self.snapshot and self.connected:
            self.window.sync(self.snapshot)
        else:
            self.window.set_disconnected(self.header.get_label())
        self.refresh()

    def _on_window_closed(self, _):
        self.window = None


class HeadsetWindow(Gtk.Window):
    def __init__(self, app: App):
        super().__init__(title="Sony headphones")
        self.app = app
        self.set_default_size(460, 0)
        self.set_resizable(False)
        self.set_icon_name(ICON)
        self._pending_eq = 0
        self._pending_amb = 0
        self._slot_names: tuple[str, ...] = ()
        self.sliders: list[Gtk.Scale] = []

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=18)
        self.add(root)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.get_style_context().add_class("dim-label")
        root.pack_start(self.status, False, False, 0)

        self.noise_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.noise_section.pack_start(self._heading("Noise control"), False, False, 0)
        mode_box = Gtk.Box(spacing=0)
        mode_box.get_style_context().add_class("linked")
        self.mode_buttons: dict[str, Gtk.RadioButton] = {}
        group = None
        for mode, label in (("nc", "Noise cancelling"), ("ambient", "Ambient"), ("off", "Off")):
            btn = Gtk.RadioButton.new_with_label_from_widget(group, label)
            btn.set_mode(False)
            group = group or btn
            btn.connect("toggled", self._on_mode, mode)
            self.mode_buttons[mode] = btn
            mode_box.pack_start(btn, True, True, 0)
        self.noise_section.pack_start(mode_box, False, False, 0)

        amb = Gtk.Grid(column_spacing=12, row_spacing=8)
        amb.attach(Gtk.Label(label="Ambient level", xalign=0), 0, 0, 1, 1)
        self.level = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, ncasm.MIN_LEVEL, ncasm.MAX_LEVEL, 1)
        self.level.set_hexpand(True)
        self.level.set_digits(0)
        self.level.connect("value-changed", self._on_level)
        amb.attach(self.level, 1, 0, 1, 1)
        amb.attach(Gtk.Label(label="Focus on voice", xalign=0), 0, 1, 1, 1)
        self.voice = Gtk.Switch(halign=Gtk.Align.START)
        self.voice.connect("notify::active", self._on_voice)
        amb.attach(self.voice, 1, 1, 1, 1)
        self.noise_section.pack_start(amb, False, False, 0)
        root.pack_start(self.noise_section, False, False, 0)

        self.eq_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.eq_section.pack_start(self._heading("Equalizer"), False, False, 0)
        self.preset = Gtk.ComboBoxText()
        self.preset.connect("changed", self._on_preset)
        self.eq_section.pack_start(self.preset, False, False, 0)
        self.bands = Gtk.Box(spacing=6, homogeneous=True)
        self.eq_section.pack_start(self.bands, False, False, 0)
        note = Gtk.Label(label="Moving a slider saves the curve to the Custom preset.", xalign=0, wrap=True)
        note.get_style_context().add_class("dim-label")
        self.eq_section.pack_start(note, False, False, 0)
        root.pack_start(self.eq_section, False, False, 0)

    @staticmethod
    def _heading(text):
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
        return label

    def show_message(self, message):
        self.status.set_text(message)

    def set_disconnected(self, message):
        self.status.set_text(message)
        self.noise_section.set_sensitive(False)
        self.eq_section.set_sensitive(False)

    def _rebuild_bands(self, caps: eq.EqCaps):
        for child in self.bands.get_children():
            self.bands.remove(child)
        self.sliders = []
        for name in caps.slot_names:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            s = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, caps.min_db, caps.max_db, 1)
            s.set_inverted(True)
            s.set_digits(0)
            s.set_value_pos(Gtk.PositionType.TOP)
            s.set_size_request(-1, 200)
            s.add_mark(0, Gtk.PositionType.RIGHT, None)
            s.connect("value-changed", self._on_band)
            self.sliders.append(s)
            col.pack_start(s, True, True, 0)
            col.pack_start(Gtk.Label(label=slot_label(name), justify=Gtk.Justification.CENTER), False, False, 0)
            self.bands.pack_start(col, True, True, 0)
        self.preset.remove_all()
        for name in caps.preset_names:
            self.preset.append(name, PRESET_LABELS.get(name, name))
        self._slot_names = caps.slot_names
        self.bands.show_all()

    def sync(self, snap: Snapshot):
        caps = snap.caps
        self.app.syncing = True
        try:
            battery = f" · battery {snap.battery.percent}% ({snap.battery.charging})" if snap.battery else ""
            self.status.set_text(f"{snap.name}{battery}")
            self.set_title(snap.name)

            self.noise_section.set_visible(caps.ncasm)
            self.noise_section.set_sensitive(True)
            if snap.ncasm:
                self.mode_buttons[snap.ncasm.mode].set_active(True)
                if not self._pending_amb:
                    self.level.set_value(snap.ncasm.level)
                self.voice.set_active(snap.ncasm.voice)

            self.eq_section.set_visible(caps.eq is not None)
            self.eq_section.set_sensitive(True)
            if caps.eq and caps.eq.slot_names != self._slot_names:
                self._rebuild_bands(caps.eq)
            if snap.eq:
                name = eq.PRESET_NAMES.get(snap.eq.preset)
                if name:
                    self.preset.set_active_id(name)
                if not self._pending_eq:
                    for s, db in zip(self.sliders, snap.eq.levels):
                        s.set_value(db)
        finally:
            self.app.syncing = False

    def _on_mode(self, btn, mode):
        if btn.get_active() and not self.app.syncing:
            self.app.set_mode(mode)

    def _on_voice(self, sw, _):
        if not self.app.syncing:
            self.app.set_ambient(voice=sw.get_active())

    def _on_level(self, _):
        if self.app.syncing:
            return
        if self._pending_amb:
            GLib.source_remove(self._pending_amb)
        self._pending_amb = GLib.timeout_add(SLIDER_DEBOUNCE_MS, self._flush_level)

    def _flush_level(self):
        self._pending_amb = 0
        self.app.set_ambient(level=int(self.level.get_value()))
        return False

    def _on_preset(self, combo):
        name = combo.get_active_id()
        if name and not self.app.syncing:
            self.app.set_preset(name)

    def _on_band(self, _):
        if self.app.syncing:
            return
        if self._pending_eq:
            GLib.source_remove(self._pending_eq)
        self._pending_eq = GLib.timeout_add(SLIDER_DEBOUNCE_MS, self._flush_bands)

    def _flush_bands(self):
        self._pending_eq = 0
        self.app.set_levels([int(s.get_value()) for s in self.sliders])
        return False


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    App()
    Gtk.main()


if __name__ == "__main__":
    main()
