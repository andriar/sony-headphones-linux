// Sony Headphones — Quick Settings toggle for noise control, with the battery level as subtitle.
// Talks to the headset through the `sonyeq` CLI (see README for why not D-Bus).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

const APP_DESKTOP_ID = 'io.github.andriar.SonyHeadphones.desktop';
const ICON = 'audio-headphones-symbolic';
const MODES = [
    ['nc', 'Noise Canceling'],
    ['ambient', 'Ambient Sound'],
    ['off', 'Off'],
];
// The toggle is one column wide: like Power Mode, the title names the feature, the subtitle the state.
const SHORT = {nc: 'Canceling', ambient: 'Ambient', off: 'Off'};
const CHARGING = {charging: ', charging', charged: ', fully charged'};
const POLL_SECONDS = 300;
const RECONNECT_DELAY_MS = 3000;

function findProgram(name) {
    const candidates = [
        GLib.find_program_in_path(name),
        // GNOME Shell's PATH often lacks ~/.local/bin, where `pip install --user` puts scripts.
        GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', name]),
        `/usr/local/bin/${name}`,
        `/usr/bin/${name}`,
    ];
    return candidates.find(p => p && GLib.file_test(p, GLib.FileTest.IS_EXECUTABLE)) ?? null;
}

// Runs `sonyeq --json …` one call at a time. The CLI also takes a per-user lock, so the
// tray, the app and this extension never talk to the headset at the same moment.
class SonyCli {
    constructor() {
        this._cancellable = new Gio.Cancellable();
        this._queue = Promise.resolve();
    }

    run(...args) {
        const job = this._queue.then(() => this._exec(args));
        this._queue = job.catch(() => {});
        return job;
    }

    async _exec(args) {
        const cli = findProgram('sonyeq');
        if (!cli)
            return {error: 'sonyeq is not installed', missing: true, disconnected: true};
        const proc = Gio.Subprocess.new([cli, '--json', ...args],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        const [stdout] = await proc.communicate_utf8_async(null, this._cancellable);
        try {
            return JSON.parse(stdout);
        } catch {
            return {error: stdout.trim() || `sonyeq exited with ${proc.get_exit_status()}`, disconnected: false};
        }
    }

    destroy() {
        this._cancellable.cancel();
    }
}

const HeadphonesToggle = GObject.registerClass(
class HeadphonesToggle extends QuickMenuToggle {
    _init(cli) {
        super._init({title: 'Noise Control', iconName: ICON, toggleMode: false, visible: false});
        this._cli = cli;
        this._lastOn = 'nc';
        this._mode = null;
        this._battery = '';

        this.menu.setHeader(ICON, 'Sony Headphones');
        this._items = {};
        for (const [mode, label] of MODES) {
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => this.setMode(mode));
            this.menu.addMenuItem(item);
            this._items[mode] = item;
        }
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Headphone Settings', () => this._openApp());

        this.connect('clicked', () => this.setMode(this.checked ? 'off' : this._lastOn));
    }

    async refresh() {
        this._show(await this._cli.run('status'));
    }

    async setMode(mode) {
        if (mode === this._mode)
            return;
        this._showMode(mode); // answer the click right away; the headset takes about a second
        const state = await this._cli.run('anc', mode);
        if (state.error)
            await this.refresh();
        else
            this._showMode(state.mode);
    }

    _show(data) {
        if (data.error) {
            if (data.missing)
                console.warn('Sony Headphones: the sonyeq command was not found; install sony-headphones-linux');
            // Hide while the headset is off or not paired, like the Bluetooth toggle without an adapter.
            // If it is connected but not answering (phone app holds the channel), say so instead.
            this.visible = !data.disconnected;
            this.subtitle = data.disconnected ? '' : 'Not responding';
            return;
        }
        this.visible = data.ncasm !== null;
        this._battery = data.battery ? `${data.battery.percent}%` : '';
        const charging = data.battery ? CHARGING[data.battery.charging] ?? '' : '';
        this.menu.setHeader(ICON, data.name, this._battery ? `Battery ${this._battery}${charging}` : '');
        if (data.ncasm)
            this._showMode(data.ncasm.mode);
    }

    _showMode(mode) {
        this._mode = mode;
        if (mode !== 'off')
            this._lastOn = mode;
        this.checked = mode !== 'off';
        this.subtitle = [SHORT[mode], this._battery].filter(Boolean).join(' · ');
        for (const [m, item] of Object.entries(this._items))
            item.setOrnament(m === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
    }

    _openApp() {
        Main.overview.hide();
        Main.panel.closeQuickSettings();
        const app = Shell.AppSystem.get_default().lookup_app(APP_DESKTOP_ID);
        if (app) {
            app.activate();
            return;
        }
        const exe = findProgram('sonyeq-app');
        if (exe)
            Gio.Subprocess.new([exe], Gio.SubprocessFlags.NONE);
        else
            Main.notify('Sony Headphones', 'The Sony Headphones app is not installed.');
    }
});

const HeadphonesIndicator = GObject.registerClass(
class HeadphonesIndicator extends SystemIndicator {
    _init(cli) {
        super._init();
        this.toggle = new HeadphonesToggle(cli);
        this.quickSettingsItems.push(this.toggle);
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class SonyHeadphonesExtension extends Extension {
    enable() {
        this._cli = new SonyCli();
        this._indicator = new HeadphonesIndicator(this._cli);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        // Re-read whenever Quick Settings opens, so the toggle is never stale when you look at it.
        this._menuOpenId = Main.panel.statusArea.quickSettings.menu.connect('open-state-changed',
            (_menu, open) => {
                if (open)
                    this._refresh();
            });
        this._busSubscription = Gio.DBus.system.signal_subscribe('org.bluez',
            'org.freedesktop.DBus.Properties', 'PropertiesChanged', null, 'org.bluez.Device1',
            Gio.DBusSignalFlags.NONE, (...args) => this._onBluez(args[5]));
        this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, POLL_SECONDS, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._refresh();
    }

    disable() {
        Main.panel.statusArea.quickSettings.menu.disconnect(this._menuOpenId);
        Gio.DBus.system.signal_unsubscribe(this._busSubscription);
        GLib.source_remove(this._pollId);
        if (this._reconnectId)
            GLib.source_remove(this._reconnectId);
        this._cli.destroy();
        this._indicator.destroy();
        this._cli = this._indicator = this._menuOpenId = this._busSubscription = null;
        this._pollId = this._reconnectId = 0;
    }

    _onBluez(params) {
        const [, changed] = params.deepUnpack();
        if (!('Connected' in changed))
            return;
        if (this._reconnectId)
            GLib.source_remove(this._reconnectId);
        // A fresh connection needs a moment before the control channel answers.
        const delay = changed.Connected.unpack() ? RECONNECT_DELAY_MS : 0;
        this._reconnectId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._reconnectId = 0;
            this._refresh();
            return GLib.SOURCE_REMOVE;
        });
    }

    _refresh() {
        this._indicator?.toggle.refresh().catch(e => {
            if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                console.error('Sony Headphones:', e);
        });
    }
}
