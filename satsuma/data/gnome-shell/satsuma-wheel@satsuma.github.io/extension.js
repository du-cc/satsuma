// Satsuma Wheel: GNOME on Wayland doesn't let apps read the pointer or place
// their own windows, so this moves Satsuma's wheel window under the pointer
// as it opens and focuses it.
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_ID = 'io.github.satsuma.Satsuma';
const TITLE = 'Satsuma Wheel';
const MARGIN = 8;

export default class SatsumaWheelExtension extends Extension {
    enable() {
        this._timeouts = new Set();
        this._createdId = global.display.connect('window-created',
            (_display, win) => this._onWindowCreated(win));
    }

    disable() {
        if (this._createdId)
            global.display.disconnect(this._createdId);
        this._createdId = 0;
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
    }

    _isWheel(win) {
        const appId = win.get_gtk_application_id?.() ?? win.get_wm_class();
        return (appId === APP_ID || win.get_wm_class() === APP_ID) && win.get_title() === TITLE;
    }

    _onWindowCreated(win) {
        // Remember where the pointer was when the shortcut was pressed.
        const [px, py] = global.get_pointer();
        const actor = win.get_compositor_private();
        if (!actor)
            return;
        // Hide until moved so the wheel doesn't flash at the centre first.
        const maybeWheel = win.get_wm_class() === APP_ID || win.get_title() === TITLE;
        if (maybeWheel)
            actor.opacity = 0;
        const id = actor.connect('first-frame', () => {
            actor.disconnect(id);
            if (!this._isWheel(win)) {
                actor.opacity = 255;
                return;
            }
            this._place(win, px, py);
            Main.activateWindow(win);
            const timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 40, () => {
                this._timeouts.delete(timeout);
                actor.opacity = 255;
                return GLib.SOURCE_REMOVE;
            });
            this._timeouts.add(timeout);
        });
    }

    _place(win, px, py) {
        const rect = win.get_frame_rect();
        const monitor = global.display.get_current_monitor();
        const area = win.get_work_area_for_monitor(monitor);
        let x = Math.round(px - rect.width / 2);
        let y = Math.round(py - rect.height / 2);
        x = Math.max(area.x + MARGIN, Math.min(x, area.x + area.width - rect.width - MARGIN));
        y = Math.max(area.y + MARGIN, Math.min(y, area.y + area.height - rect.height - MARGIN));
        win.move_frame(true, x, y);
    }
}
