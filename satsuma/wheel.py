"""The radial wheel: petals around a hole, one per format, tool or action.

The geometry matches Tangerine's wheel: petals start at the top (or at
the left for three items) and run clockwise, with rounded outer corners
and a gap between neighbours.
"""

from __future__ import annotations

import math
import os
import struct
import tempfile
import wave

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gtk, Pango, PangoCairo  # noqa: E402

from . import formats as F  # noqa: E402

INNER = 48 * 0.8 * 1.1 + 8 * 0.8
OUTER = 166 * 0.8 * 0.9
BACKDROP = OUTER + 8 * 0.8
GAP = 5 * 0.8
CORNER = 28 * 0.8 * 0.9
LABEL_INNER = 50 * 0.8
HALF = BACKDROP + 10  # half the widget's logical size before scaling

ACCENT = (1.0, 0.353, 0.0)  # tangerine orange #ff5a00

MODE_TITLES = {"conversions": "CONVERT", "tools": "TOOLS", "actions": "ACTIONS"}
EMPTY_TEXT = {
    "conversions": "No conversions for this format.\nAlt for tools · Ctrl for actions",
    "tools": "No tools for this format.\nShift to convert · Ctrl for actions",
    "actions": "No actions available.",
}


def petal_angle(index: int, count: int) -> float:
    return (math.pi if count == 3 else math.pi / 2) - 2 * math.pi * index / count


def _polar(radius: float, angle: float) -> tuple[float, float]:
    return math.cos(angle) * radius, -math.sin(angle) * radius


def _gap_angle(radius: float) -> float:
    return math.asin(min(0.95, GAP / 2 / max(radius, GAP / 2 + 0.01)))


def petal_path(cr, index: int, count: int):
    """Trace one petal on a cairo context centred on the wheel."""
    a = petal_angle(index, count)
    span = 2 * math.pi / count
    lo, hi = a - span / 2, a + span / 2
    r = _gap_angle
    d, i = lo + r(OUTER), hi - r(OUTER)
    c, g = lo + r(INNER), hi - r(INNER)
    f = min(0.15, (i - d) * 0.24)
    u = min(0.18, (g - c) * 0.18)
    v = INNER / max(0.01, math.cos(u))
    y = v + INNER * math.tan(u)
    o = OUTER - CORNER

    def quad(control, end):
        x0, y0 = cr.get_current_point()
        (qx, qy), (x1, y1) = control, end
        cr.curve_to(x0 + 2 / 3 * (qx - x0), y0 + 2 / 3 * (qy - y0),
                    x1 + 2 / 3 * (qx - x1), y1 + 2 / 3 * (qy - y1), x1, y1)

    cr.new_path()
    cr.move_to(*_polar(y, lo + r(y)))
    cr.line_to(*_polar(o, lo + r(o)))
    quad(_polar(OUTER, d), _polar(OUTER, d + f))
    # Outer edge: increasing maths angle is anticlockwise on screen.
    cr.arc_negative(0, 0, OUTER, -(d + f), -(i - f))
    quad(_polar(OUTER, i), _polar(o, hi - r(o)))
    cr.line_to(*_polar(y, hi - r(y)))
    quad(_polar(v, hi - r(v)), _polar(INNER, g - u))
    cr.arc(0, 0, INNER, -(g - u), -(c + u))
    quad(_polar(v, lo + r(v)), _polar(y, lo + r(y)))
    cr.close_path()


def hit_index(x: float, y: float, count: int) -> int | None:
    """Petal under a point given relative to the wheel centre (unscaled)."""
    dist = math.hypot(x, y)
    if not count or dist < INNER or dist > OUTER:
        return None
    angle = math.atan2(-y, x)
    span = 2 * math.pi / count
    offset = ((petal_angle(0, count) - angle) % (2 * math.pi) + 2 * math.pi) % (2 * math.pi)
    index = round(offset / span) % count
    delta = math.atan2(math.sin(angle - petal_angle(index, count)),
                       math.cos(angle - petal_angle(index, count)))
    if abs(delta) > span / 2 - math.asin(GAP / 2 / dist):
        return None
    return index


_PLAYBACK = None


def playback_available() -> bool:
    """Whether Gtk.MediaFile can play. GTK aborts the process when GStreamer's
    playbin is missing, so check before creating any media stream."""
    global _PLAYBACK
    if _PLAYBACK is None:
        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            _PLAYBACK = Gst.ElementFactory.find("playbin") is not None
        except (ImportError, ValueError):
            _PLAYBACK = False
    return _PLAYBACK


class TickSound:
    """The short click Tangerine plays as the highlight moves."""

    def __init__(self):
        self.media = None
        if not playback_available():
            return
        path = os.path.join(GLib.get_user_cache_dir(), "satsuma", "tick.wav")
        try:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._write(path)
            self.media = Gtk.MediaFile.new_for_filename(path)
            self.media.set_volume(0.35)
        except (OSError, GLib.Error):
            self.media = None

    @staticmethod
    def _write(path: str):
        rate, frames = 44100, int(44100 * 0.018)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        os.close(fd)
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            data = b"".join(
                struct.pack("<h", int(12000 * math.exp(-n / (frames / 5)) *
                                      math.sin(2 * math.pi * 2200 * n / rate)))
                for n in range(frames))
            w.writeframes(data)
        os.replace(tmp, path)

    def play(self):
        if self.media is None:
            return
        self.media.seek(0)
        self.media.play()


class Wheel(Gtk.Widget):
    """Radial menu widget. Emits ``activated(mode, key)``."""

    __gtype_name__ = "SatsumaWheel"
    __gsignals__ = {
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "mode-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "cancelled": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, scale: float = 1.15, sound: bool = True):
        super().__init__()
        self.scale = scale
        self.mode = "conversions"
        self.items: list[str] = []
        self.disabled: set[str] = set()
        self.selected: int | None = None
        self.pressed = False
        self.sound = TickSound() if sound else None
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_accessible_role(Gtk.AccessibleRole.MENU)

        self._pointer_xy = None
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda *_: self._pointer_xy is not None and self.select(None))
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    # --- state ------------------------------------------------------------------------

    def set_items(self, mode: str, items: list[str], disabled: set[str] | None = None):
        self.mode = mode
        self.items = list(items)
        self.disabled = set(disabled or ())
        self.selected = None
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [{"conversions": "Choose a conversion format",
                               "tools": "Choose a file tool",
                               "actions": "Choose a file action"}[mode]])
        self.queue_draw()
        self.emit("selection-changed")

    def select(self, index: int | None, *, sound: bool = True):
        if index is not None and self.items and self.items[index] in self.disabled:
            index = None
        if index == self.selected:
            return
        self.selected = index
        if index is not None and sound and self.sound:
            self.sound.play()
        self.queue_draw()
        self.emit("selection-changed")

    def selected_key(self) -> str | None:
        return None if self.selected is None else self.items[self.selected]

    def activate_selected(self) -> bool:
        key = self.selected_key()
        if key is None or key in self.disabled:
            return False
        self.emit("activated", self.mode, key)
        return True

    def index_at(self, x: float, y: float) -> int | None:
        w, h = self.get_width(), self.get_height()
        index = hit_index((x - w / 2) / self.scale, (y - h / 2) / self.scale, len(self.items))
        return index

    def describe_selection(self) -> str:
        key = self.selected_key()
        if key is None:
            return ""
        return F.item_name(self.mode, key)

    # --- input ------------------------------------------------------------------------------

    def pointer_moved(self, x: float, y: float) -> bool:
        """True when the pointer really moved. GTK also reports motion after
        redraws, which must not undo a selection made with the keyboard."""
        if self._pointer_xy is not None and abs(self._pointer_xy[0] - x) < 1 and abs(self._pointer_xy[1] - y) < 1:
            return False
        first = self._pointer_xy is None
        self._pointer_xy = (x, y)
        return not first or self.index_at(x, y) is not None

    def _on_motion(self, _ctl, x, y):
        if self.pointer_moved(x, y):
            self.select(self.index_at(x, y))

    def _on_pressed(self, _gesture, _n, x, y):
        self.grab_focus()
        self.pressed = True
        self.select(self.index_at(x, y))

    def _on_released(self, _gesture, _n, x, y):
        if self.pressed and self.index_at(x, y) is not None and self.index_at(x, y) == self.selected:
            self.activate_selected()
        self.pressed = False

    def _on_key(self, _ctl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        count = len(self.items)
        if name in ("Shift_L", "Shift_R"):
            self.emit("mode-requested", "conversions")
            return True
        if name in ("Control_L", "Control_R"):
            self.emit("mode-requested", "actions")
            return True
        if name in ("Alt_L", "Alt_R", "Meta_L", "Meta_R", "ISO_Level3_Shift"):
            self.emit("mode-requested", "tools")
            return True
        if name == "Tab" or name == "ISO_Left_Tab":
            order = ["conversions", "tools", "actions"]
            step = -1 if name == "ISO_Left_Tab" else 1
            self.emit("mode-requested", order[(order.index(self.mode) + step) % 3])
            return True
        if name == "Escape":
            self.emit("cancelled")
            return True
        if not count:
            return False
        if name in ("Right", "Down", "KP_Right", "KP_Down", "Left", "Up", "KP_Left", "KP_Up"):
            step = 1 if name.endswith(("Right", "Down")) else -1
            start = self.selected if self.selected is not None else (-1 if step > 0 else 0)
            for k in range(1, count + 1):
                candidate = (start + step * k) % count
                if self.items[candidate] not in self.disabled:
                    self.select(candidate)
                    break
            return True
        if name in ("Home", "End"):
            enabled = [i for i, key in enumerate(self.items) if key not in self.disabled]
            if enabled:
                self.select(enabled[0] if name == "Home" else enabled[-1])
            return True
        if name in ("Return", "KP_Enter", "space"):
            return self.activate_selected()
        if len(name) == 1 and name.isalnum():
            # Type-ahead: the first item whose label starts with the letter.
            titles = [F.item_title(self.mode, k) for k in self.items]
            for k in range(1, count + 1):
                candidate = ((self.selected or 0) + k) % count if self.selected is not None else k - 1
                if titles[candidate].upper().startswith(name.upper()):
                    self.select(candidate)
                    break
            return True
        return False

    # --- layout and drawing -------------------------------------------------------------------

    def do_measure(self, orientation, for_size):
        size = int(2 * HALF * self.scale)
        return size, size, -1, -1

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def _palette(self):
        dark = Adw.StyleManager.get_default().get_dark()
        if dark:
            return {"backdrop": (0.22, 0.22, 0.24, 0.92), "rim": (1, 1, 1, 0.08),
                    "petal": (0.32, 0.32, 0.35, 1), "petal_edge": (1, 1, 1, 0.06),
                    "text": (0.96, 0.96, 0.97), "muted": (0.62, 0.62, 0.66),
                    "disabled": (0.45, 0.45, 0.48), "hole": (0.17, 0.17, 0.19, 1),
                    "pill": (0.27, 0.27, 0.3, 1)}
        return {"backdrop": (0.86, 0.88, 0.89, 0.9), "rim": (1, 1, 1, 0.7),
                "petal": (0.965, 0.972, 0.975, 1), "petal_edge": (0, 0, 0, 0.06),
                "text": (0.11, 0.11, 0.12), "muted": (0.45, 0.46, 0.48),
                "disabled": (0.7, 0.7, 0.72), "hole": (0.93, 0.94, 0.95, 1),
                "pill": (1, 1, 1, 1)}

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        pal = self._palette()
        cr.translate(w / 2, h / 2)
        cr.scale(self.scale, self.scale)

        # Glass backdrop with a soft drop shadow.
        for i in range(10, 0, -1):
            cr.arc(0, 2.5, BACKDROP + i, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, 0.012)
            cr.fill()
        cr.arc(0, 0, BACKDROP, 0, 2 * math.pi)
        cr.set_source_rgba(*pal["backdrop"])
        cr.fill_preserve()
        cr.set_source_rgba(*pal["rim"])
        cr.set_line_width(1)
        cr.stroke()

        count = len(self.items)
        if not count:
            self._draw_center_text(cr, EMPTY_TEXT[self.mode], pal["muted"], 10.5, OUTER * 1.6)
            return

        dense = count > 6
        for index, key in enumerate(self.items):
            highlighted = index == self.selected
            disabled = key in self.disabled
            petal_path(cr, index, count)
            if highlighted:
                cr.set_source_rgb(*ACCENT)
            else:
                cr.set_source_rgba(*pal["petal"])
            cr.fill_preserve()
            cr.set_source_rgba(*((1, 1, 1, 0.25) if highlighted else pal["petal_edge"]))
            cr.set_line_width(0.8)
            cr.stroke()

            lx, ly = _polar(LABEL_INNER + (OUTER - LABEL_INNER) * 0.56, petal_angle(index, count))
            color = (1, 1, 1) if highlighted else (pal["disabled"] if disabled else pal["text"])
            title = F.item_title(self.mode, key)
            size = 8.2 if self.mode != "conversions" else (10.5 if dense else 11.5)
            if self.mode != "conversions":
                ly += 9.5
            self._draw_label(cr, title, lx, ly, color, size, bold=True)

        # Centre hole and the pill naming the highlighted item.
        cr.arc(0, 0, INNER - 4, 0, 2 * math.pi)
        cr.set_source_rgba(*pal["hole"])
        cr.fill()
        key = self.selected_key()
        text = F.item_title(self.mode, key) if key else MODE_TITLES[self.mode]
        self._draw_pill(cr, text, pal, active=key is not None)

        # Symbolic icons for tools and actions, drawn on top.
        if self.mode != "conversions":
            self._snapshot_icons(snapshot, w, h, pal)

    def _layout(self, cr, text, size, bold, width=None):
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string(f"{'Bold' if bold else ''} {size}px")
        desc.set_family("Inter, Cantarell, Adwaita Sans, sans-serif")
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_wrap(Pango.WrapMode.WORD)
        return layout

    def _draw_label(self, cr, text, x, y, color, size, bold=False):
        layout = self._layout(cr, text, size, bold)
        _ink, logical = layout.get_pixel_extents()
        cr.move_to(x - logical.width / 2, y - logical.height / 2)
        cr.set_source_rgb(*color)
        PangoCairo.show_layout(cr, layout)

    def _draw_center_text(self, cr, text, color, size, width):
        layout = self._layout(cr, text, size, False, width)
        _ink, logical = layout.get_pixel_extents()
        cr.move_to(-width / 2, -logical.height / 2)
        cr.set_source_rgb(*color)
        PangoCairo.show_layout(cr, layout)

    def _draw_pill(self, cr, text, pal, active):
        layout = self._layout(cr, text, 8.6, True)
        _ink, logical = layout.get_pixel_extents()
        pw = min(INNER * 2 - 10, max(46, logical.width + 18))
        ph = 22
        x, y, r = -pw / 2, -ph / 2, ph / 2
        cr.new_sub_path()
        cr.arc(x + pw - r, 0, r, -math.pi / 2, math.pi / 2)
        cr.arc(x + r, 0, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*pal["pill"])
        cr.fill()
        if logical.width > pw - 8:
            layout.set_width(int((pw - 8) * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            _ink, logical = layout.get_pixel_extents()
        cr.move_to(-logical.width / 2, y + (ph - logical.height) / 2)
        cr.set_source_rgb(*(pal["text"] if active else pal["muted"]))
        PangoCairo.show_layout(cr, layout)

    def _snapshot_icons(self, snapshot, w, h, pal):
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        count = len(self.items)
        size = 17
        for index, key in enumerate(self.items):
            names = [n for n in F.item_icons(self.mode, key) if theme.has_icon(n)]
            if not names:
                continue
            paintable = theme.lookup_icon(names[0], names[1:], size, self.get_scale_factor(),
                                          Gtk.TextDirection.NONE, 0)
            lx, ly = _polar(LABEL_INNER + (OUTER - LABEL_INNER) * 0.56, petal_angle(index, count))
            px = w / 2 + (lx * self.scale) - size / 2
            py = h / 2 + ((ly - 7.5) * self.scale) - size / 2
            if index == self.selected:
                rgb = (1, 1, 1)
            elif key in self.disabled:
                rgb = pal["disabled"]
            else:
                rgb = pal["text"]
            color = Gdk.RGBA()
            color.red, color.green, color.blue, color.alpha = rgb[0], rgb[1], rgb[2], 1.0
            snapshot.save()
            snapshot.translate(Graphene.Point().init(px, py))
            paintable.snapshot_symbolic(snapshot, size, size, [color])
            snapshot.restore()
