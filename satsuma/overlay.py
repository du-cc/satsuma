"""The hotkey wheel: a small transparent window holding the wheel.

GNOME on Wayland lets apps neither read the pointer nor place their own
windows, so the Satsuma GNOME Shell extension (data/gnome-shell/) moves this
window, recognised by its title, so the wheel's centre sits under the pointer
and gives it focus. Without the extension GNOME opens it centred.
"""

from __future__ import annotations

import os

from gi.repository import Adw, GLib, Gtk

from . import backends as B
from . import formats as F
from .wheel import Wheel

# The GNOME Shell extension finds the wheel window by this title.
OVERLAY_TITLE = "Satsuma Wheel"
BAND_HEIGHT = 70
PANEL_WIDTH = 360

MODES = ["conversions", "tools", "actions"]
MODE_LABELS = {"conversions": "Convert", "tools": "Tools", "actions": "Actions"}
MODE_KEYS = {"conversions": "Shift", "tools": "Alt", "actions": "Ctrl"}


def targets_for(mode: str, files: list[str]) -> list[str]:
    formats = [F.detect_format(p) for p in files]
    if mode == "conversions":
        return F.selection_targets(formats)
    if mode == "tools":
        return F.tool_targets(formats)
    return F.action_targets()


class Overlay(Gtk.Window):
    # Its own CSS node name (set below), so themes' `window { background }`
    # rules in ~/.config/gtk-4.0/gtk.css can't paint the transparent corners.
    __gtype_name__ = "SatsumaOverlay"

    def __init__(self, app, files: list[str], mode: str | None = None):
        super().__init__(application=app, decorated=False, title=OVERLAY_TITLE)
        self.app = app
        self.files = files
        self.remove_css_class("background")
        self.closing = False

        self.wheel = Wheel(scale=app.settings["wheel_scale"], sound=app.settings["sound"])
        self.wheel.connect("activated", self._on_activated)
        self.wheel.connect("mode-requested", lambda _w, m: self.set_mode(m))
        self.wheel.connect("cancelled", lambda *_: self.dismiss())
        self.wheel.connect("selection-changed", lambda *_: self._update_hint())

        self.chips = {}
        chip_box = Gtk.Box(css_classes=["linked", "satsuma-chips"], halign=Gtk.Align.CENTER)
        leader = None
        for m in MODES:
            chip = Gtk.ToggleButton(css_classes=["satsuma-chip"], focus_on_click=False, can_focus=False)
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=MODE_LABELS[m]))
            row.append(Gtk.Label(label=MODE_KEYS[m], css_classes=["satsuma-key", "caption"]))
            chip.set_child(row)
            if leader:
                chip.set_group(leader)
            else:
                leader = chip
            chip.connect("toggled", lambda c, m=m: c.get_active() and m != self.wheel.mode and self.set_mode(m))
            chip_box.append(chip)
            self.chips[m] = chip
        name = os.path.basename(files[0]) if len(files) == 1 else f"{len(files)} items"
        self.title = Gtk.Label(label=name, css_classes=["satsuma-overlay-title"], ellipsize=3,
                               max_width_chars=36)
        self.hint = Gtk.Label(css_classes=["satsuma-overlay-hint"], wrap=True, max_width_chars=44,
                              justify=Gtk.Justification.CENTER, lines=2,
                              ellipsize=3)

        # The wheel sits exactly in the middle of the window (equal-height bands
        # above and below), so centring the window on the pointer centres the wheel.
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END,
                      height_request=BAND_HEIGHT, margin_bottom=6)
        top.append(self.title)
        top.append(chip_box)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START,
                         height_request=BAND_HEIGHT, margin_top=6)
        bottom.append(self.hint)
        self.panel = Gtk.CenterBox(orientation=Gtk.Orientation.VERTICAL, opacity=0,
                                   width_request=PANEL_WIDTH)
        self.panel.set_start_widget(top)
        self.panel.set_center_widget(self.wheel)
        self.panel.set_end_widget(bottom)
        # Pin the window to PANEL_WIDTH: the clamp caps the natural width (long
        # hints wrap instead of widening the window) and width_request is the floor.
        self.set_child(Adw.Clamp(child=self.panel, maximum_size=PANEL_WIDTH,
                                 tightening_threshold=PANEL_WIDTH))
        self.set_resizable(False)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_pointer)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click_outside)
        self.panel.add_controller(click)
        self.connect("notify::is-active", self._on_active_changed)
        self.connect("map", lambda *_: self._fade_in())

        self.set_mode(mode or app.settings["default_wheel"], initial=True)

    def popup(self):
        self.present()
        self.wheel.grab_focus()

    def _fade_in(self):
        target = Adw.CallbackAnimationTarget.new(lambda v: self.panel.set_opacity(v))
        animation = Adw.TimedAnimation.new(self.panel, 0, 1, 120, target)
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        animation.play()

    def _on_pointer(self, _ctl, x, y):
        ok, point = self.compute_point(self.wheel, _pt(x, y))
        if ok and self.wheel.pointer_moved(point.x, point.y):
            self.wheel.select(self.wheel.index_at(point.x, point.y))

    def _on_click_outside(self, gesture, _n, x, y):
        # Clicks on the transparent corners close the wheel, like clicking away.
        for widget in (self.wheel, self.title, *self.chips.values()):
            ok, bounds = widget.compute_bounds(self.panel)
            if ok and bounds.contains_point(_pt(x, y)):
                return
        self.dismiss()

    def _on_active_changed(self, *_):
        # Clicking another window or switching apps dismisses the wheel.
        if self.get_mapped() and not self.is_active() and not self.closing:
            GLib.timeout_add(80, lambda: (not self.is_active() and self.dismiss()) and False)

    def dismiss(self):
        if self.closing:
            return
        self.closing = True
        self.close()

    # --- modes --------------------------------------------------------------------------------

    def set_mode(self, mode: str, initial: bool = False):
        items = targets_for(mode, self.files)
        if initial and not items:
            mode = next((m for m in MODES if targets_for(m, self.files)), "actions")
            items = targets_for(mode, self.files)
        formats = [F.detect_format(p) for p in self.files]
        disabled = set()
        if mode == "conversions":
            disabled = {t for t in items if not all(B.is_available(f, t) for f in formats)}
        self.wheel.set_items(mode, items, disabled)
        if not self.chips[mode].get_active():
            self.chips[mode].set_active(True)
        self._update_hint()

    def _update_hint(self):
        key = self.wheel.selected_key()
        if key and key in self.wheel.disabled:
            text = "Converter not installed — see Preferences → Converters"
        elif key:
            text = f"Enter or click to {F.item_phrase(self.wheel.mode, key)}"
        elif self.wheel.items:
            text = "Shift · Alt · Ctrl switch wheels · Esc closes"
        else:
            text = ""
        self.hint.set_label(text)
        self.hint.set_visible(bool(text))

    def _on_activated(self, _wheel, mode, key):
        files = list(self.files)
        self.closing = True
        self.set_visible(False)
        self.app.perform(mode, key, files, None)
        self.close()


Overlay.set_css_name("satsuma-overlay")


def _pt(x, y):
    from gi.repository import Graphene
    return Graphene.Point().init(x, y)
