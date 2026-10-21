"""Sony Headphones: a GTK 4 + libadwaita window for noise control, the equalizer and battery."""
import dataclasses
import math
import shutil
import signal
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import cairo  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__, bluez, eq  # noqa: E402
from .gui import (APP_ID, PRESET_LABELS, RECONNECT_DELAY_MS, Absence, BluezWatch, Worker,  # noqa: E402
                  concerns, load)
from .headset import Headset, HeadsetError, Snapshot  # noqa: E402

ICON = "audio-headphones-symbolic"
POLL_SECONDS = 60
STALE_SECONDS = 15
LOADING_DELAY_MS = 400  # a load that finishes sooner goes straight to the controls, without a flash
SLIDER_DEBOUNCE_MS = 350
CURVE_HEIGHT = 56
SLIDER_HEIGHT = 168
MODES = (
    ("nc", "Noise Canceling", "Blocks out surrounding sound"),
    ("ambient", "Ambient Sound", "Lets surrounding sound in"),
    ("off", "Off", "No noise canceling or ambient sound"),
)
SLOT_TITLES = {"clear-bass": "Clear Bass", "400": "400 Hz", "1k": "1 kHz", "2.5k": "2.5 kHz",
               "6.3k": "6.3 kHz", "16k": "16 kHz"}
CHARGING_TEXT = {"charging": ", charging", "charged": ", fully charged"}


def battery_text(snap: Snapshot) -> str:
    if snap.battery is None:
        return ""
    return f"{snap.battery.percent}% battery{CHARGING_TEXT.get(snap.battery.charging, '')}"


def bluetooth_settings_command() -> list[str] | None:
    if shutil.which("gnome-control-center"):
        return ["gnome-control-center", "bluetooth"]
    if shutil.which("blueman-manager"):
        return ["blueman-manager"]
    return None


class EqCurve(Gtk.DrawingArea):
    """Smoothed line through the band levels, in the accent color, filled towards 0 dB."""

    def __init__(self):
        super().__init__(content_height=CURVE_HEIGHT, hexpand=True)
        self.add_css_class("accent")  # libadwaita: color = accent color, follows the system
        self.levels: list[int] = []
        self.lo, self.hi = -10, 10
        self.set_draw_func(self._draw)

    def set_levels(self, levels, lo: int, hi: int):
        self.levels, self.lo, self.hi = list(levels), lo, hi
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        n = len(self.levels)
        if n < 2 or self.hi <= self.lo:
            return
        c = self.get_color()
        pad = 6

        def y(db):
            return pad + (self.hi - db) / (self.hi - self.lo) * (height - 2 * pad)

        pts = [(width * (i + 0.5) / n, y(db)) for i, db in enumerate(self.levels)]
        zero = y(0)
        cr.set_source_rgba(c.red, c.green, c.blue, 0.3)
        cr.set_line_width(1)
        cr.set_dash([2, 3])
        cr.move_to(0, zero)
        cr.line_to(width, zero)
        cr.stroke()
        cr.set_dash([])

        def trace():
            cr.move_to(0, pts[0][1])
            cr.line_to(*pts[0])
            for i in range(n - 1):  # Catmull-Rom through the band points, as cubic Béziers
                p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
                p3 = pts[min(i + 2, n - 1)]
                cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                            p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, *p2)
            cr.line_to(width, pts[-1][1])

        trace()
        cr.line_to(width, zero)
        cr.line_to(0, zero)
        cr.close_path()
        cr.set_source_rgba(c.red, c.green, c.blue, 0.16)
        cr.fill()
        trace()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.set_line_width(2)
        cr.stroke()


class Spinner(Gtk.DrawingArea):
    """Thin rotating arc in the accent color, like libadwaita 1.6's spinner.

    Drawn here rather than Gtk.Spinner, which some icon themes render as a static cog.
    """

    SIZE = 32

    def __init__(self):
        super().__init__(content_width=self.SIZE, content_height=self.SIZE, halign=Gtk.Align.CENTER)
        self.add_css_class("accent")
        self._angle = 0.0
        self._tick = 0
        self.set_draw_func(self._draw)
        self.connect("map", self._start)
        self.connect("unmap", self._stop)

    def _start(self, *_):
        if Gtk.Settings.get_default().props.gtk_enable_animations and not self._tick:
            self._tick = self.add_tick_callback(self._on_tick)

    def _stop(self, *_):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def _on_tick(self, _widget, clock):
        self._angle = (clock.get_frame_time() / 1e6 * 2 * math.pi / 1.2) % (2 * math.pi)
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, width, height):
        c = self.get_color()
        r = min(width, height) / 2 - 2
        cr.set_line_width(3)
        cr.set_line_cap(cairo.LineCap.ROUND)
        cr.set_source_rgba(c.red, c.green, c.blue, 0.18)
        cr.arc(width / 2, height / 2, r, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.arc(width / 2, height / 2, r, self._angle, self._angle + 0.6 * math.pi)
        cr.stroke()


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, headset: Headset | None = None):
        super().__init__(application=app, title="Sony Headphones", icon_name=ICON)
        self.set_default_size(460, 780)
        self.set_size_request(360, 360)
        self.headset = headset or Headset()
        self.worker = Worker(self.headset)
        self.snapshot: Snapshot | None = None
        self.absence: Absence | None = None
        self.syncing = False  # True while widgets are updated from headset state
        self._loading = self._load_again = False
        self._loaded_at = 0.0
        self._pending = {"eq": 0, "level": 0}
        self._reconnect_timer = 0
        self._loading_timer = 0
        self._slot_names: tuple[str, ...] = ()
        self._preset_ids: list[str] = []
        self._curve_from = 0
        self.sliders: list[Gtk.Scale] = []
        self._bt_settings = bluetooth_settings_command()

        self._build()
        self.watch = BluezWatch(self._on_bluez_change)
        self._poll_id = GLib.timeout_add_seconds(POLL_SECONDS, self._poll)
        self.connect("notify::is-active", self._on_active)
        self.connect("close-request", self._on_close)
        self.show_busy("Looking for your headphones…", delay=True)
        self.refresh()

    # ---- layout -------------------------------------------------------------
    def _build(self):
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_: self.refresh())
        self.add_action(refresh)

        self.title = Adw.WindowTitle(title="Sony Headphones")
        header = Adw.HeaderBar(title_widget=self.title)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh",
                                         action_name="win.refresh")
        header.pack_start(self.refresh_button)
        menu = Gio.Menu()
        if hasattr(Gtk.ShortcutsGroup, "add_shortcut"):  # GTK 4.14+
            self.set_help_overlay(self._build_shortcuts())
            menu.append("_Keyboard Shortcuts", "win.show-help-overlay")
        menu.append("_About Sony Headphones", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main Menu"))

        self.status = Adw.StatusPage(icon_name=ICON)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.primary = Gtk.Button(css_classes=["pill", "suggested-action"])
        self.primary.connect("clicked", self._on_primary)
        self.secondary = Gtk.Button(label="Bluetooth Settings", css_classes=["pill"])
        self.secondary.connect("clicked", lambda _: self._open_bluetooth_settings())
        actions.append(self.primary)
        actions.append(self.secondary)
        self.status.set_child(actions)

        # Waiting is not a problem to explain: a small spinner and one quiet line, no status page.
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.loading_label = Gtk.Label(css_classes=["dim-label"])
        loading.append(Spinner())
        loading.append(self.loading_label)

        page = Adw.PreferencesPage()
        page.add(self._build_noise())
        page.add(self._build_eq())

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Gtk.Box(), "blank")
        self.stack.add_named(loading, "loading")
        self.stack.add_named(self.status, "status")
        self.stack.add_named(page, "controls")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

    def _build_noise(self) -> Adw.PreferencesGroup:
        self.noise_group = Adw.PreferencesGroup(title="Noise Control")
        self.mode_checks: dict[str, Gtk.CheckButton] = {}
        first = None
        for mode, title, subtitle in MODES:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=first)
            first = first or check
            check.connect("toggled", self._on_mode, mode)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.mode_checks[mode] = check
            self.noise_group.add(row)

        self.level_row = Adw.ActionRow(title="Ambient Level")
        self.level = Gtk.Scale(adjustment=Gtk.Adjustment(lower=1, upper=20, step_increment=1, page_increment=1),
                               digits=0, draw_value=True, value_pos=Gtk.PositionType.LEFT,
                               hexpand=True, valign=Gtk.Align.CENTER, width_request=160)
        self.level.update_property([Gtk.AccessibleProperty.LABEL], ["Ambient level"])
        self.level.connect("value-changed", self._on_level)
        self.level_row.add_suffix(self.level)
        self.noise_group.add(self.level_row)

        self.voice_row = Adw.SwitchRow(title="Focus on Voice",
                                       subtitle="Lets voices through more clearly than other sound")
        self.voice_row.connect("notify::active", self._on_voice)
        self.noise_group.add(self.voice_row)
        return self.noise_group

    def _build_eq(self) -> Adw.PreferencesGroup:
        self.eq_group = Adw.PreferencesGroup(
            title="Equalizer", description="Moving a slider saves your curve to the Custom preset.")
        self.preset_model = Gtk.StringList()
        self.preset_row = Adw.ComboRow(title="Preset", model=self.preset_model)
        self.preset_row.connect("notify::selected", self._on_preset)
        self.eq_group.add(self.preset_row)

        self.bands_row = Adw.PreferencesRow(activatable=False, focusable=False)
        self.bands_row.update_property([Gtk.AccessibleProperty.LABEL], ["Equalizer bands"])
        self.bands_box = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.bands_row.set_child(self.bands_box)
        self.eq_group.add(self.bands_row)
        self.curve = EqCurve()
        return self.eq_group

    @staticmethod
    def _build_shortcuts() -> Gtk.ShortcutsWindow:
        group = Gtk.ShortcutsGroup(title="General")
        for accel, title in (("<Ctrl>r", "Refresh"), ("<Ctrl>w", "Close Window"), ("<Ctrl>q", "Quit"),
                             ("<Ctrl>question", "Keyboard Shortcuts")):
            group.add_shortcut(Gtk.ShortcutsShortcut(accelerator=accel, title=title))
        section = Gtk.ShortcutsSection()
        section.add_group(group)
        win = Gtk.ShortcutsWindow(modal=True)
        win.add_section(section)
        return win

    def _band_column(self, name: str, caps: eq.EqCaps) -> Gtk.Box:
        title = SLOT_TITLES.get(name, name)
        scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, digits=0, draw_value=True,
                          has_origin=False,
                          value_pos=Gtk.PositionType.TOP, vexpand=True, height_request=SLIDER_HEIGHT,
                          halign=Gtk.Align.CENTER, tooltip_text=title,
                          adjustment=Gtk.Adjustment(lower=caps.min_db, upper=caps.max_db,
                                                    step_increment=1, page_increment=2))
        scale.set_format_value_func(lambda _s, v: f"{round(v):+d}" if round(v) else "0")
        scale.add_mark(0, Gtk.PositionType.RIGHT, None)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        scale.connect("value-changed", self._on_band)
        self.sliders.append(scale)
        label = Gtk.Label(label=title.replace(" ", "\n", 1), justify=Gtk.Justification.CENTER,
                          css_classes=["caption"])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.append(scale)
        col.append(label)
        return col

    def _build_bands(self, caps: eq.EqCaps):
        while (child := self.bands_box.get_first_child()) is not None:
            self.bands_box.remove(child)
        self.sliders = []
        names = list(caps.slot_names)
        if names and names[0] == "clear-bass":
            # Clear Bass is a separate bass boost, not a band of the curve: keep it apart.
            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            side.append(Gtk.Box(height_request=CURVE_HEIGHT))
            side.append(self._band_column(names.pop(0), caps))
            self.bands_box.append(side)
            self.bands_box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        main.append(self.curve)
        cols = Gtk.Box(homogeneous=True)
        for name in names:
            cols.append(self._band_column(name, caps))
        main.append(cols)
        self.bands_box.append(main)
        self._curve_from = len(self.sliders) - len(names)

        self._preset_ids = list(caps.preset_names)
        self.preset_model.splice(0, self.preset_model.get_n_items(),
                                 [PRESET_LABELS.get(n, n) for n in self._preset_ids])
        self._slot_names = caps.slot_names

    def _update_curve(self):
        caps = self.snapshot.caps.eq if self.snapshot else None
        if caps:
            self.curve.set_levels([round(s.get_value()) for s in self.sliders[self._curve_from:]],
                                  caps.min_db, caps.max_db)

    # ---- states -------------------------------------------------------------
    def _set_title(self, title: str, subtitle: str = ""):
        self.title.set_title(title)
        self.title.set_subtitle(subtitle)
        self.set_title(title)

    def _show_page(self, name: str):
        if self._loading_timer:
            GLib.source_remove(self._loading_timer)
            self._loading_timer = 0
        self.stack.set_visible_child_name(name)

    def _show_status(self, title, description, primary=None, settings=False):
        self.status.set_title(title)
        self.status.set_description(description)
        self.primary.set_visible(primary is not None)
        if primary:
            self.primary.set_label(primary)
        self.secondary.set_visible(settings and self._bt_settings is not None)
        self._show_page("status")

    def show_busy(self, text: str, delay: bool = False):
        """Show that something is in progress; with delay, only if it is still going after a moment."""
        self._set_title("Sony Headphones")
        self.loading_label.set_label(text)
        if not delay:
            self._show_page("loading")
            return
        self._show_page("blank")

        def reveal():
            self._loading_timer = 0
            self.stack.set_visible_child_name("loading")
            return GLib.SOURCE_REMOVE

        self._loading_timer = GLib.timeout_add(LOADING_DELAY_MS, reveal)

    def show_absence(self, a: Absence):
        self.absence, self.snapshot = a, None
        self._set_title("Sony Headphones")
        name = a.device.name if a.device else "your headphones"
        # Short titles: the window is narrow and the device name reads better in the sentence below.
        if a.kind == "none":
            self._show_status("No Headphones Paired",
                              "Pair your Sony headphones in Bluetooth settings. They show up here once connected.",
                              primary="Open Bluetooth Settings" if self._bt_settings else None)
        elif a.kind == "unsupported":
            self._show_status("Not Supported Yet",
                              f"{name} uses Sony’s older control protocol. For now only models with "
                              "the newer one, like the WH-1000XM5, work.")
        elif a.kind == "disconnected":
            self._show_status("Not Connected",
                              f"Turn on your {name}. It usually reconnects on its own within a few seconds.",
                              primary="Connect", settings=True)
        else:
            print(f"sonyeq: {a.message}", file=sys.stderr)
            self._show_status("Not Responding",
                              f"Close the Sony app on your phone, then try again. {name} accepts "
                              "one controller at a time.", primary="Try Again")

    def show_snapshot(self, snap: Snapshot):
        self.snapshot, self.absence = snap, None
        self._set_title(snap.name, battery_text(snap))
        self._show_page("controls")
        self._sync()

    def _sync(self):
        snap = self.snapshot
        caps = snap.caps
        self.syncing = True
        try:
            self.noise_group.set_visible(caps.ncasm)
            if snap.ncasm:
                self.mode_checks[snap.ncasm.mode].set_active(True)
                self._show_ambient(snap.ncasm.mode == "ambient")
                if not self._pending["level"]:
                    self.level.set_value(snap.ncasm.level)
                self.voice_row.set_active(snap.ncasm.voice)

            self.eq_group.set_visible(caps.eq is not None)
            if caps.eq and caps.eq.slot_names != self._slot_names:
                self._build_bands(caps.eq)
            if snap.eq:
                name = eq.PRESET_NAMES.get(snap.eq.preset)
                if name in self._preset_ids:
                    self.preset_row.set_selected(self._preset_ids.index(name))
                if not self._pending["eq"]:
                    for s, db in zip(self.sliders, snap.eq.levels):
                        s.set_value(db)
                self._update_curve()
        finally:
            self.syncing = False

    def _show_ambient(self, on: bool):
        self.level_row.set_visible(on)
        self.voice_row.set_visible(on)

    # ---- loading ------------------------------------------------------------
    def refresh(self):
        if self._loading:
            self._load_again = True
            return
        self._loading = True
        self.refresh_button.set_sensitive(False)
        self.worker.submit(load, self._on_loaded, self._on_load_error)

    def _on_loaded(self, result):
        self._loading = False
        self._loaded_at = time.monotonic()
        self.refresh_button.set_sensitive(True)
        if isinstance(result, Snapshot):
            self.show_snapshot(result)
        else:
            self.show_absence(result)
        if self._load_again:
            self._load_again = False
            self.refresh()

    def _on_load_error(self, err):
        self._on_loaded(Absence("unreachable", str(err), self.headset.device))

    def _poll(self):
        if self.get_visible():
            self.refresh()
        return True

    def _on_active(self, *_):
        if self.is_active() and time.monotonic() - self._loaded_at > STALE_SECONDS:
            self.refresh()

    def _on_close(self, *_):
        GLib.source_remove(self._poll_id)
        return False

    def _known_device(self) -> bluez.SonyDevice | None:
        return self.headset.device or (self.absence.device if self.absence else None)

    def _on_bluez_change(self, path, connected):
        dev = self._known_device()
        if not concerns(dev, path):
            return
        if self._reconnect_timer:
            GLib.source_remove(self._reconnect_timer)
            self._reconnect_timer = 0
        if connected:
            if self.snapshot is None and dev is not None:
                self.show_busy(f"Connecting to {dev.name}…")
            # Give the headset a moment to bring up its profiles before opening the control channel.
            self._reconnect_timer = GLib.timeout_add(RECONNECT_DELAY_MS, self._reconnect)
        else:
            self.refresh()

    def _reconnect(self):
        self._reconnect_timer = 0
        self.refresh()
        return False

    # ---- commands -----------------------------------------------------------
    def _command(self, fn, on_done):
        self.worker.submit(fn, on_done, self._on_command_error)

    def _on_command_error(self, err):
        if not (isinstance(err, HeadsetError) and err.disconnected):
            print(f"sonyeq: {err}", file=sys.stderr)
            self.toasts.add_toast(Adw.Toast(title="Couldn’t apply the change. Showing the current settings."))
        self.refresh()

    def _replace(self, **changes):
        if self.snapshot:
            self.snapshot = dataclasses.replace(self.snapshot, **changes)
            self._sync()

    def _on_primary(self, _button):
        a = self.absence
        if a and a.kind == "none":
            self._open_bluetooth_settings()
        elif a and a.kind == "disconnected" and a.device:
            self._connect(a)
        else:
            self.show_busy("Looking for your headphones…")
            self.refresh()

    def _connect(self, a: Absence):
        self.show_busy(f"Connecting to {a.device.name}…")
        mac = a.device.mac

        def done(ok):
            if ok:
                self._reconnect_timer = self._reconnect_timer or GLib.timeout_add(RECONNECT_DELAY_MS,
                                                                                  self._reconnect)
            else:
                self.show_absence(a)
                self.toasts.add_toast(Adw.Toast(title="Couldn’t connect. Turn the headphones on and keep them close."))

        def failed(err):
            print(f"sonyeq: {err}", file=sys.stderr)
            done(False)

        self.worker.submit(lambda _h: bluez.connect(mac), done, failed)

    def _open_bluetooth_settings(self):
        try:
            Gio.Subprocess.new(self._bt_settings, Gio.SubprocessFlags.NONE)
        except (GLib.Error, TypeError) as e:
            print(f"sonyeq: cannot open Bluetooth settings: {e}", file=sys.stderr)

    def _on_mode(self, check, mode):
        if self.syncing or not check.get_active():
            return
        self._show_ambient(mode == "ambient")
        self._command(lambda h: h.set_ncasm(mode=mode), lambda s: self._replace(ncasm=s))

    def _on_voice(self, row, _):
        if not self.syncing:
            voice = row.get_active()
            self._command(lambda h: h.set_ncasm(voice=voice), lambda s: self._replace(ncasm=s))

    def _on_level(self, _):
        if not self.syncing:
            self._debounce("level", self._flush_level)

    def _flush_level(self):
        self._pending["level"] = 0
        level = round(self.level.get_value())
        self._command(lambda h: h.set_ncasm(level=level), lambda s: self._replace(ncasm=s))
        return False

    def _on_preset(self, row, _):
        index = row.get_selected()
        if self.syncing or index >= len(self._preset_ids):
            return
        preset = eq.PRESETS[self._preset_ids[index]]
        self._command(lambda h: h.set_preset(preset), lambda s: self._replace(eq=s))

    def _on_band(self, _):
        if self.syncing:
            return
        self._update_curve()
        self._debounce("eq", self._flush_bands)

    def _flush_bands(self):
        self._pending["eq"] = 0
        levels = [round(s.get_value()) for s in self.sliders]
        self._command(lambda h: h.set_levels(levels), lambda s: self._replace(eq=s))
        return False

    def _debounce(self, key, fn):
        if self._pending[key]:
            GLib.source_remove(self._pending[key])
        self._pending[key] = GLib.timeout_add(SLIDER_DEBOUNCE_MS, fn)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, handler in (("about", self._on_about), ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("win.refresh", ["<Ctrl>r", "F5"])
        self.set_accels_for_action("window.close", ["<Ctrl>w"])
        self.set_accels_for_action("win.show-help-overlay", ["<Ctrl>question"])

    def do_activate(self):
        (self.get_active_window() or Window(self)).present()

    def _on_about(self, *_):
        icon = APP_ID if Gtk.IconTheme.get_for_display(self.get_active_window().get_display()).has_icon(APP_ID) \
            else "audio-headphones"
        about = dict(application_name="Sony Headphones", application_icon=icon,
                     version=__version__, developer_name="sony-headphones-linux contributors",
                     website="https://github.com/andriar/sony-headphones-linux",
                     issue_url="https://github.com/andriar/sony-headphones-linux/issues",
                     license_type=Gtk.License.MIT_X11,
                     comments="Unofficial app, not affiliated with or endorsed by Sony.")
        if hasattr(Adw, "AboutDialog"):  # libadwaita 1.5+
            Adw.AboutDialog(**about).present(self.get_active_window())
        else:
            Adw.AboutWindow(transient_for=self.get_active_window(), **about).present()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(App().run(sys.argv))


if __name__ == "__main__":
    main()
