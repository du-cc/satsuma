"""Parameter dialogs and editors for the file tools."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading

import cairo
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import backends as B
from . import formats as F
from . import tools as T
from .wheel import playback_available


def open_tool_dialog(parent: Gtk.Widget, tool: str, files: list[str], on_accept) -> bool:
    """Show the dialog for ``tool``; ``on_accept(params)`` runs on Save.

    Returns False when the tool needs no dialog (the caller runs it directly).
    """
    builder = BUILDERS.get(tool)
    if builder is None:
        return False
    try:
        dialog = builder(files, on_accept)
    except Exception as exc:  # noqa: BLE001 - report preview failures without crashing
        alert = Adw.AlertDialog(heading=F.TOOLS[tool][1], body=f"Could not open the editor: {exc}")
        alert.add_response("ok", "OK")
        alert.present(parent or _host_window(parent))
        return True
    dialog.present(parent)
    return dialog


# --- Shared scaffolding ---------------------------------------------------------------------

class ToolDialog:
    """A tool's settings: a sheet for small forms, a window for editors.

    Adw.Dialog is limited to its parent's size, which is too small for
    canvases when the main window is a compact drop zone.
    """

    def __init__(self, title: str, on_accept, *, accept_label="Save", width=420, height=-1):
        self.title, self.width, self.height = title, width, height
        self.big = width > 560
        self._on_accept = on_accept
        self._cleanup: list[str] = []
        self._closed: list = []
        self.window = self.dialog = None
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title=title))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.accept_button = Gtk.Button(label=accept_label, css_classes=["suggested-action"])
        self.accept_button.connect("clicked", self._accept)
        header.pack_start(cancel)
        header.pack_end(self.accept_button)
        self.view = Adw.ToolbarView()
        self.view.add_top_bar(header)
        self.toasts = Adw.ToastOverlay(child=self.view)
        self.params = lambda: {}

    def set_body(self, widget: Gtk.Widget):
        self.view.set_content(widget)

    def connect(self, signal: str, callback):
        assert signal == "closed"
        self._closed.append(callback)

    def present(self, parent: Gtk.Widget | None):
        root = parent.get_root() if parent else None
        if self.big or parent is None:  # no parent: opened from the overlay
            self.window = Adw.Window(title=self.title, modal=root is not None, transient_for=root,
                                     application=Gio.Application.get_default(),
                                     default_width=self.width, default_height=self.height,
                                     content=self.toasts)
            esc = Gtk.ShortcutController()
            esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                              Gtk.CallbackAction.new(lambda *_: self.close() or True)))
            self.window.add_controller(esc)
            self.window.connect("close-request", lambda *_: self._fire_closed() or False)
            self.window.present()
        else:
            self.dialog = Adw.Dialog(title=self.title, content_width=self.width)
            self.dialog.set_child(self.toasts)
            self.dialog.connect("closed", lambda *_: self._fire_closed())
            self.dialog.present(parent)

    def parent_window(self) -> Gtk.Window | None:
        if self.window:
            return self.window
        return self.dialog.get_root() if self.dialog else None

    def close(self):
        if self.window:
            self.window.close()
        elif self.dialog:
            self.dialog.close()

    def temp_dir(self) -> str:
        path = tempfile.mkdtemp(prefix="satsuma-ui-")
        self._cleanup.append(path)
        return path

    def _accept(self, *_):
        try:
            params = self.params()
        except ValueError as exc:
            self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(exc))))
            return
        self.close()
        self._on_accept(params)

    def _fire_closed(self):
        callbacks, self._closed = self._closed, []
        for callback in callbacks:
            callback(self)
        for path in self._cleanup:
            shutil.rmtree(path, ignore_errors=True)
        self._cleanup = []


def _host_window(parent):
    """An invisible-until-needed window to host an alert when there is no parent."""
    if parent is not None:
        return parent
    window = Adw.Window(default_width=1, default_height=1, decorated=False,
                        application=Gio.Application.get_default())
    window.present()
    return window


def settings_page(*groups: Adw.PreferencesGroup) -> Gtk.Widget:
    """Groups in a scroller that asks for its natural height (fits small sheets)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12,
                  margin_bottom=18, margin_start=12, margin_end=12)
    for group in groups:
        box.append(group)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                  max_content_height=560, vexpand=True)
    scroller.set_child(Adw.Clamp(child=box, maximum_size=560))
    return scroller


def combo(title: str, labels: list[str], selected: int = 0, subtitle: str = "") -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels), selected=selected)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def spin(title: str, lo: float, hi: float, step: float, value: float, digits: int = 0,
         subtitle: str = "") -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    row.set_digits(digits)
    row.set_value(value)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def color_row(title: str, value: str) -> tuple[Adw.ActionRow, Gtk.ColorDialogButton]:
    row = Adw.ActionRow(title=title)
    button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
    rgba = Gdk.RGBA()
    rgba.parse(value)
    button.set_rgba(rgba)
    row.add_suffix(button)
    row.set_activatable_widget(button)
    return row, button


def rgba_hex(button: Gtk.ColorDialogButton) -> str:
    c = button.get_rgba()
    return "#{:02x}{:02x}{:02x}".format(*(int(round(v * 255)) for v in (c.red, c.green, c.blue)))


def file_row(title: str, parent_getter, on_pick, filters=("image/*",)) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle="None", activatable=True)
    row.add_suffix(Gtk.Image(icon_name="document-open-symbolic"))

    def pick(*_):
        dialog = Gtk.FileDialog(title=title)
        store = Gio.ListStore.new(Gtk.FileFilter)
        flt = Gtk.FileFilter()
        for mime in filters:
            flt.add_mime_type(mime)
        store.append(flt)
        dialog.set_filters(store)

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return
            row.set_subtitle(GLib.markup_escape_text(gfile.get_basename()))
            on_pick(gfile.get_path())
        dialog.open(parent_getter(), None, done)
    row.connect("activated", pick)
    return row


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}"


def parse_times(text: str) -> list[float]:
    """'0:12, 1:05.5, 90' → [12.0, 65.5, 90.0]"""
    values = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        seconds = 0.0
        try:
            for piece in part.split(":"):
                seconds = seconds * 60 + float(piece)
        except ValueError as exc:
            raise ValueError(f"“{part}” is not a time") from exc
        values.append(seconds)
    return values


def run_in_thread(work, done):
    """Run ``work()`` off the main loop and hand its result (or exception) to ``done``."""
    def target():
        try:
            result, error = work(), None
        except Exception as exc:  # noqa: BLE001
            result, error = None, exc
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=target, daemon=True).start()


def preview_copy(src: str, directory: str, max_side: int = 1100) -> str:
    """A downscaled PNG for fast previews and canvases."""
    out = os.path.join(directory, "preview.png")
    fmt = F.detect_format(src)
    if fmt == "svg" and B.tool("rsvg-convert"):
        subprocess.run([B.tool("rsvg-convert"), "-f", "png", "-o", out, src], check=True)
        src = out
    subprocess.run([B.require("magick", "ImageMagick"), src + "[0]", "-auto-orient",
                    "-colorspace", "sRGB", "-resize", f"{max_side}x{max_side}>", out],
                   check=True, capture_output=True)
    return out


def image_size(path: str) -> tuple[int, int]:
    exe = B.tool("magick")
    cmd = [exe, "identify"] if os.path.basename(exe) == "magick" else [exe.replace("convert", "identify")]
    out = subprocess.run(cmd + ["-format", "%w %h", path + "[0]"], capture_output=True, text=True).stdout
    w, h = (int(v) for v in out.split()[:2])
    return w, h


def oriented_size(path: str) -> tuple[int, int]:
    """Size after EXIF auto-orientation, matching what the tools operate on."""
    exe = B.tool("magick")
    cmd = [exe, "identify"] if os.path.basename(exe) == "magick" else [exe.replace("convert", "identify")]
    out = subprocess.run(cmd + ["-format", "%w %h %[orientation]", path + "[0]"],
                         capture_output=True, text=True).stdout.split()
    w, h = int(out[0]), int(out[1])
    if len(out) > 2 and out[2] in ("LeftTop", "RightTop", "RightBottom", "LeftBottom"):
        w, h = h, w
    return w, h


def video_frame(src: str, t: float, out: str, max_side: int = 1100) -> str:
    subprocess.run(B.ffmpeg_base() + ["-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
                                      "-vf", f"scale='min({max_side},iw)':-2", out],
                   check=True, capture_output=True)
    return out


def waveform(src: str, out: str, width: int = 1400, height: int = 220, color: str = "ff5a00") -> str:
    subprocess.run(B.ffmpeg_base() + ["-i", src, "-filter_complex",
                                      f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:"
                                      f"colors=0x{color}", "-frames:v", "1", out],
                   check=True, capture_output=True)
    return out


class LivePreview(Gtk.Picture):
    """Re-renders a tool on a small copy of the image whenever settings change."""

    def __init__(self, render):
        super().__init__(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True,
                         can_shrink=True, css_classes=["satsuma-preview"])
        self.render = render  # params -> output path (runs on a worker thread)
        self._pending = None
        self._generation = 0

    def refresh(self, params: dict):
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(160, self._start, dict(params))

    def _start(self, params):
        self._pending = None
        self._generation += 1
        generation = self._generation

        def done(path, error):
            if generation == self._generation and path:
                self.set_filename(path)
            return False
        run_in_thread(lambda: self.render(params), done)
        return False


# --- Region canvas (crop, redact) -----------------------------------------------------------------

class RegionCanvas(Gtk.DrawingArea):
    """Shows an image and lets you draw/move/resize rectangles in image pixels."""

    HANDLE = 9

    def __init__(self, multiple: bool, full_size: tuple[int, int]):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.multiple = multiple
        self.full_w, self.full_h = full_size
        self.surface: cairo.ImageSurface | None = None
        self.rects: list[list[float]] = []  # in full-size image pixels
        self.active: int | None = None
        self.aspect: float | None = None
        self.style = "crop" if not multiple else "blur"
        self._drag = None
        self.on_change = lambda: None
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_: self._end())
        self.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def set_image(self, png: str):
        self.surface = cairo.ImageSurface.create_from_png(png)
        self.queue_draw()

    # Coordinate mapping between widget and full-size image pixels.
    def _frame(self):
        w, h = self.get_width(), self.get_height()
        scale = min((w - 24) / self.full_w, (h - 24) / self.full_h)
        dw, dh = self.full_w * scale, self.full_h * scale
        return (w - dw) / 2, (h - dh) / 2, scale

    def to_image(self, x, y):
        ox, oy, s = self._frame()
        return (min(max((x - ox) / s, 0), self.full_w), min(max((y - oy) / s, 0), self.full_h))

    def to_widget(self, x, y):
        ox, oy, s = self._frame()
        return ox + x * s, oy + y * s

    def _draw(self, _area, cr, w, h):
        ox, oy, s = self._frame()
        if self.surface:
            cr.save()
            cr.translate(ox, oy)
            cr.scale(self.full_w * s / self.surface.get_width(), self.full_h * s / self.surface.get_height())
            cr.set_source_surface(self.surface, 0, 0)
            cr.paint()
            cr.restore()
        if self.style == "crop" and self.rects:
            x, y, rw, rh = self.rects[0]
            cr.rectangle(ox, oy, self.full_w * s, self.full_h * s)
            cr.rectangle(ox + x * s, oy + y * s, rw * s, rh * s)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.fill()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
            # Rule of thirds
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.set_line_width(1)
            for k in (1, 2):
                cr.move_to(ox + (x + rw * k / 3) * s, oy + y * s)
                cr.line_to(ox + (x + rw * k / 3) * s, oy + (y + rh) * s)
                cr.move_to(ox + x * s, oy + (y + rh * k / 3) * s)
                cr.line_to(ox + (x + rw) * s, oy + (y + rh * k / 3) * s)
            cr.stroke()
        for i, (x, y, rw, rh) in enumerate(self.rects):
            px, py, pw, ph = ox + x * s, oy + y * s, rw * s, rh * s
            if self.style != "crop":
                if self.style == "solid":
                    cr.set_source_rgba(0, 0, 0, 0.85)
                else:
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.55)
                cr.rectangle(px, py, pw, ph)
                cr.fill()
            cr.set_source_rgb(1, 0.353, 0) if i == self.active or self.style == "crop" else cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2)
            cr.rectangle(px, py, pw, ph)
            cr.stroke()
            if i == self.active or self.style == "crop":
                for hx, hy in ((px, py), (px + pw, py), (px, py + ph), (px + pw, py + ph)):
                    cr.arc(hx, hy, 5, 0, 2 * math.pi)
                    cr.set_source_rgb(1, 1, 1)
                    cr.fill_preserve()
                    cr.set_source_rgb(1, 0.353, 0)
                    cr.set_line_width(1.5)
                    cr.stroke()

    def _hit(self, wx, wy):
        for i in reversed(range(len(self.rects))):
            x, y, w, h = self.rects[i]
            corners = {"tl": (x, y), "tr": (x + w, y), "bl": (x, y + h), "br": (x + w, y + h)}
            for name, (cx, cy) in corners.items():
                px, py = self.to_widget(cx, cy)
                if math.hypot(px - wx, py - wy) <= self.HANDLE:
                    return i, name
            px, py = self.to_widget(x, y)
            qx, qy = self.to_widget(x + w, y + h)
            if px <= wx <= qx and py <= wy <= qy:
                return i, "move"
        return None, None

    def _begin(self, _g, wx, wy):
        self.grab_focus()
        index, part = self._hit(wx, wy)
        ix, iy = self.to_image(wx, wy)
        if index is None:
            if not self.multiple:
                self.rects = []
            self.rects.append([ix, iy, 0, 0])
            index, part = len(self.rects) - 1, "new"
        self.active = index
        self._drag = (index, part, ix, iy, list(self.rects[index]))
        self.queue_draw()

    def _update(self, gesture, dx, dy):
        if not self._drag:
            return
        index, part, ix, iy, orig = self._drag
        ok, sx, sy = gesture.get_start_point()
        cx, cy = self.to_image(sx + dx, sy + dy)
        x, y, w, h = orig
        if part == "move":
            nx = min(max(x + cx - ix, 0), self.full_w - w)
            ny = min(max(y + cy - iy, 0), self.full_h - h)
            self.rects[index] = [nx, ny, w, h]
        else:
            if part == "new":
                ax, ay = ix, iy
            else:
                ax = x + w if "l" in part else x
                ay = y + h if "t" in part else y
            nw, nh = cx - ax, cy - ay
            if self.aspect:
                if abs(nw) / self.aspect > abs(nh):
                    nh = math.copysign(abs(nw) / self.aspect, nh or 1)
                else:
                    nw = math.copysign(abs(nh) * self.aspect, nw or 1)
            rx, ry = min(ax, ax + nw), min(ay, ay + nh)
            rw, rh = abs(nw), abs(nh)
            rx, ry = max(0, rx), max(0, ry)
            rw, rh = min(rw, self.full_w - rx), min(rh, self.full_h - ry)
            self.rects[index] = [rx, ry, rw, rh]
        self.queue_draw()
        self.on_change()

    def _end(self):
        if self._drag:
            index = self._drag[0]
            if self.rects[index][2] < 4 or self.rects[index][3] < 4:
                if self._drag[1] == "new" and self.multiple:
                    del self.rects[index]
                    self.active = None
                elif self._drag[1] == "new":
                    self.rects[index] = self._drag[4] if self._drag[4][2] else [0, 0, self.full_w, self.full_h]
        self._drag = None
        self.queue_draw()
        self.on_change()

    def _key(self, _c, keyval, *_):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.multiple and self.active is not None:
            del self.rects[self.active]
            self.active = None
            self.queue_draw()
            self.on_change()
            return True
        return False

    def fit_aspect(self, aspect: float | None):
        """Centre the largest rectangle with ``aspect`` (or the whole image)."""
        self.aspect = aspect
        if aspect is None:
            self.rects = [[0, 0, self.full_w, self.full_h]]
        else:
            w, h = self.full_w, self.full_h
            cw, ch = (h * aspect, h) if w / h > aspect else (w, w / aspect)
            self.rects = [[(w - cw) / 2, (h - ch) / 2, cw, ch]]
        self.active = 0
        self.queue_draw()
        self.on_change()


ASPECT_LABELS = ["Free", "Original", "1:1", "4:5", "5:4", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]


def _aspect_value(label: str, full: tuple[int, int]) -> float | None:
    if label == "Free":
        return None
    if label == "Original":
        return full[0] / full[1]
    return T.ASPECTS[label]


def _crop_image(files, on_accept):
    if len(files) > 1:
        d = ToolDialog("Crop Images", on_accept)
        aspect = combo("Aspect ratio", ASPECT_LABELS[2:], 0, "Crops each image around its centre")
        exact = Adw.SwitchRow(title="Exact output size")
        width, height = spin("Width", 16, 20000, 1, 1080), spin("Height", 16, 20000, 1, 1080)
        group = Adw.PreferencesGroup(title=f"{len(files)} images")
        for row in (aspect, exact, width, height):
            group.add(row)

        def params():
            if exact.get_active():
                return {"width": int(width.get_value()), "height": int(height.get_value())}
            return {"aspect": ASPECT_LABELS[2:][aspect.get_selected()]}
        d.params = params
        d.set_body(settings_page(group))
        return d

    src = files[0]
    d = ToolDialog("Crop Image", on_accept, width=960, height=640)
    full = oriented_size(src)
    canvas = RegionCanvas(False, full)
    canvas.set_image(preview_copy(src, d.temp_dir()))
    aspect = combo("Aspect ratio", ASPECT_LABELS, 0)
    size_label = Adw.ActionRow(title="Selection")
    exact = Adw.SwitchRow(title="Resize to exact size")
    width, height = spin("Width", 16, 20000, 1, full[0]), spin("Height", 16, 20000, 1, full[1])

    def update_label():
        if canvas.rects:
            x, y, w, h = canvas.rects[0]
            size_label.set_subtitle(f"{int(w)} × {int(h)} at {int(x)}, {int(y)}")
    canvas.on_change = update_label
    aspect.connect("notify::selected", lambda *_: canvas.fit_aspect(
        _aspect_value(ASPECT_LABELS[aspect.get_selected()], full)))
    canvas.fit_aspect(None)
    group = Adw.PreferencesGroup()
    for row in (aspect, size_label, exact, width, height):
        group.add(row)

    def params():
        if not canvas.rects:
            raise ValueError("Draw the area to keep")
        result = {"rect": tuple(canvas.rects[0])}
        if exact.get_active():
            result.update(width=int(width.get_value()), height=int(height.get_value()))
            del result["rect"]
        return result
    d.params = params
    d.set_body(_split(canvas, settings_page(group)))
    return d


def _split(preview: Gtk.Widget, side: Gtk.Widget, side_width: int = 340) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    frame = Gtk.Box(css_classes=["satsuma-canvas"], hexpand=True, vexpand=True)
    frame.append(preview)
    box.append(frame)
    side.set_size_request(side_width, -1)
    # Clamp the side panel so long row subtitles can't squeeze the preview.
    clamp = Adw.Clamp(child=side, maximum_size=side_width, tightening_threshold=side_width)
    clamp.set_hexpand(False)  # stop rows' hexpand from propagating to the panel
    box.append(clamp)
    return box


STYLE_LABELS = ["Blur", "Pixelate", "Solid color"]
STYLE_KEYS = ["blur", "pixelate", "solid"]


def _redact_image(files, on_accept):
    src = files[0]
    d = ToolDialog("Redact Photo", on_accept, width=960, height=640)
    full = oriented_size(src)
    canvas = RegionCanvas(True, full)
    preview = preview_copy(src, d.temp_dir())
    canvas.set_image(preview)
    style = combo("Cover with", STYLE_LABELS, 0)
    style.connect("notify::selected", lambda *_: (setattr(canvas, "style", STYLE_KEYS[style.get_selected()]),
                                                   canvas.queue_draw()))
    color_r, color_b = color_row("Color", "#000000")
    count = Adw.ActionRow(title="Areas", subtitle="Drag on the photo to cover an area. "
                                                  "Select one and press Delete to remove it.")
    canvas.on_change = lambda: count.set_title(f"{len(canvas.rects)} area{'s' if len(canvas.rects) != 1 else ''}")
    group = Adw.PreferencesGroup()
    for row in (style, color_r, count):
        group.add(row)
    detect = Adw.PreferencesGroup(title="Detect locally")

    def add_detect(label, kind, available):
        row = Adw.ActionRow(title=label, activatable=available,
                            subtitle="" if available else "Not installed")
        if available:
            row.add_suffix(Gtk.Image(icon_name="system-search-symbolic"))

            def go(*_):
                row.set_sensitive(False)

                def done(rects, error):
                    row.set_sensitive(True)
                    if error:
                        row.set_subtitle(str(error))
                        return
                    # Detection ran on the preview; scale back to full size.
                    pw, ph = image_size(preview)
                    sx, sy = full[0] / pw, full[1] / ph
                    canvas.rects += [[x * sx, y * sy, w * sx, h * sy] for x, y, w, h in rects]
                    row.set_subtitle(f"Found {len(rects)}")
                    canvas.queue_draw()
                    canvas.on_change()
                run_in_thread(lambda: T.detect_regions(preview, kind), done)
            row.connect("activated", go)
        detect.add(row)
    add_detect("Faces", "faces", importlib.util.find_spec("cv2") is not None)
    add_detect("Text", "text", bool(B.tool("tesseract")))

    def params():
        if not canvas.rects:
            raise ValueError("Mark at least one area")
        return {"regions": [tuple(r) for r in canvas.rects], "style": STYLE_KEYS[style.get_selected()],
                "color": rgba_hex(color_b)}
    d.params = params
    d.set_body(_split(canvas, settings_page(group, detect)))
    return d


# --- Video frame canvases (crop, redact) ------------------------------------------------------------

def _video_region(files, on_accept, redact: bool):
    src = files[0]
    info = T.media_info(src)
    if not info["width"]:
        raise ValueError("No video stream found")
    d = ToolDialog("Redact Video" if redact else "Crop Video", on_accept, width=980, height=660)
    tmp = d.temp_dir()
    canvas = RegionCanvas(redact, (info["width"], info["height"]))
    canvas.set_image(video_frame(src, min(1.0, info["duration"] / 2), os.path.join(tmp, "f.png")))
    duration = max(info["duration"], 0.1)
    scrub = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, duration, 0.04)
    scrub.set_draw_value(True)
    scrub.set_format_value_func(lambda _s, v: fmt_time(v))
    scrub.set_value(min(1.0, duration / 2))
    pending = {"id": None, "n": 0}

    def scrubbed(*_):
        if pending["id"]:
            GLib.source_remove(pending["id"])

        def load():
            pending["id"] = None
            pending["n"] += 1
            n = pending["n"]
            path = os.path.join(tmp, f"f{n}.png")
            run_in_thread(lambda: video_frame(src, scrub.get_value(), path),
                          lambda p, e: (not e and n == pending["n"] and canvas.set_image(p)))
            return False
        pending["id"] = GLib.timeout_add(120, load)
    scrub.connect("value-changed", scrubbed)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=8,
                   margin_start=8, margin_end=8)
    left.append(canvas)
    left.append(scrub)
    group = Adw.PreferencesGroup()
    if redact:
        style = combo("Cover with", STYLE_LABELS, 0)
        style.connect("notify::selected", lambda *_: (setattr(canvas, "style", STYLE_KEYS[style.get_selected()]),
                                                       canvas.queue_draw()))
        color_r, color_b = color_row("Color", "#000000")
        start = spin("From (seconds)", 0, duration, 0.1, 0, 2)
        end = spin("Until (seconds)", 0, duration, 0.1, duration, 2)
        set_start = Gtk.Button(label="Use frame", valign=Gtk.Align.CENTER, css_classes=["flat"])
        set_start.connect("clicked", lambda *_: start.set_value(scrub.get_value()))
        start.add_suffix(set_start)
        set_end = Gtk.Button(label="Use frame", valign=Gtk.Align.CENTER, css_classes=["flat"])
        set_end.connect("clicked", lambda *_: end.set_value(scrub.get_value()))
        end.add_suffix(set_end)
        for row in (style, color_r, start, end):
            group.add(row)
        group.set_description("Drag on the frame to cover areas for the chosen time range.")

        def params():
            if not canvas.rects:
                raise ValueError("Mark at least one area")
            return {"regions": [tuple(r) for r in canvas.rects], "style": STYLE_KEYS[style.get_selected()],
                    "color": rgba_hex(color_b), "start": start.get_value(),
                    "end": end.get_value() if end.get_value() < duration else None}
    else:
        full = (info["width"], info["height"])
        aspect = combo("Aspect ratio", ASPECT_LABELS, 0)
        aspect.connect("notify::selected", lambda *_: canvas.fit_aspect(
            _aspect_value(ASPECT_LABELS[aspect.get_selected()], full)))
        canvas.fit_aspect(None)
        group.add(aspect)
        group.set_description("The original audio is kept.")

        def params():
            return {"rect": tuple(canvas.rects[0])}
    d.params = params
    d.set_body(_split(left, settings_page(group)))
    return d


# --- Media timeline (trim, split, snapshots, bleep) -----------------------------------------------------

class Timeline(Gtk.DrawingArea):
    """Waveform/scrub strip with a selection range, marks and a playhead."""

    def __init__(self, duration: float):
        super().__init__(hexpand=True, height_request=120, focusable=True)
        self.duration = max(duration, 0.01)
        self.wave: cairo.ImageSurface | None = None
        self.start, self.end = 0.0, self.duration
        self.marks: list[float] = []
        self.ranges: list[list[float]] = []
        self.playhead = 0.0
        self.mode = "range"  # range, marks, ranges
        self.on_seek = lambda t: None
        self.on_change = lambda: None
        self._drag = None
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_: setattr(self, "_drag", None))
        self.add_controller(drag)

    def _t(self, x):
        return min(max(x / max(1, self.get_width()), 0), 1) * self.duration

    def _x(self, t):
        return t / self.duration * self.get_width()

    def _draw(self, _a, cr, w, h):
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.12)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        if self.wave:
            cr.save()
            cr.scale(w / self.wave.get_width(), h / self.wave.get_height())
            cr.set_source_surface(self.wave, 0, 0)
            cr.paint()
            cr.restore()
        if self.mode == "range":
            cr.set_source_rgba(0, 0, 0, 0.45)
            cr.rectangle(0, 0, self._x(self.start), h)
            cr.rectangle(self._x(self.end), 0, w - self._x(self.end), h)
            cr.fill()
            cr.set_source_rgb(1, 0.353, 0)
            for t in (self.start, self.end):
                cr.rectangle(self._x(t) - 3, 0, 6, h)
            cr.fill()
        elif self.mode == "ranges":
            for a, b in self.ranges:
                cr.set_source_rgba(1, 0.353, 0, 0.35)
                cr.rectangle(self._x(a), 0, self._x(b) - self._x(a), h)
                cr.fill()
        if self.mode == "marks":
            cr.set_source_rgb(1, 0.353, 0)
            for t in self.marks:
                cr.rectangle(self._x(t) - 1.5, 0, 3, h)
            cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.rectangle(self._x(self.playhead) - 1, 0, 2, h)
        cr.fill()

    def _begin(self, _g, x, _y):
        self.grab_focus()
        t = self._t(x)
        if self.mode == "range":
            near = min((abs(self._x(self.start) - x), "start"), (abs(self._x(self.end) - x), "end"))
            self._drag = near[1] if near[0] < 14 else "seek"
        elif self.mode == "ranges":
            self.ranges.append([t, t])
            self._drag = "ranges"
        else:
            self._drag = "seek"
        if self._drag == "seek":
            self.playhead = t
            self.on_seek(t)
        self.queue_draw()

    def _update(self, gesture, dx, _dy):
        ok, sx, _sy = gesture.get_start_point()
        t = self._t(sx + dx)
        if self._drag == "start":
            self.start = min(t, self.end - 0.05)
        elif self._drag == "end":
            self.end = max(t, self.start + 0.05)
        elif self._drag == "ranges":
            a = self._t(sx)
            self.ranges[-1] = [min(a, t), max(a, t)]
        elif self._drag == "seek":
            self.playhead = t
            self.on_seek(t)
        self.queue_draw()
        self.on_change()


def _media_dialog(files, on_accept, kind: str):
    """trimVideo, trimAudio, splitVideo, videoSnapshots and redactAudio."""
    src = files[0]
    info = T.media_info(src)
    duration = info["duration"] or 0.01
    titles = {"trimVideo": "Trim Video", "trimAudio": "Trim Audio", "splitVideo": "Split Video",
              "videoSnapshots": "Save Video Frames", "redactAudio": "Bleep Audio"}
    d = ToolDialog(titles[kind], on_accept, width=960, height=660)
    tmp = d.temp_dir()
    timeline = Timeline(duration)
    video_kind = kind in ("trimVideo", "splitVideo", "videoSnapshots")
    top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=10, margin_end=10,
                  margin_bottom=10)
    if playback_available():
        media = Gtk.MediaFile.new_for_filename(src)
        if video_kind:
            top.append(Gtk.Video(media_stream=media, vexpand=True, autoplay=False))
        else:
            top.append(Gtk.Box(vexpand=True))
            top.append(Gtk.MediaControls(media_stream=media))
    else:
        # No GStreamer: scrub with still frames instead of playback.
        media = _StillMedia()
        if video_kind:
            still = Gtk.Picture(vexpand=True, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
            frames = {"n": 0}

            def show_frame(t):
                frames["n"] += 1
                path = os.path.join(tmp, f"still{frames['n'] % 3}.png")
                run_in_thread(lambda: video_frame(src, t, path),
                              lambda p, e: (not e and still.set_filename(p)))
            media.on_seek = show_frame
            show_frame(0)
            top.append(still)
        else:
            top.append(Gtk.Box(vexpand=True))
    top.append(timeline)
    timeline.on_seek = lambda t: media.seek(int(t * 1_000_000)) if media.is_seekable() else None

    def tick():
        if media.get_playing():
            timeline.playhead = media.get_timestamp() / 1_000_000
            timeline.queue_draw()
        return True
    tick_id = GLib.timeout_add(100, tick)
    d.connect("closed", lambda *_: (GLib.source_remove(tick_id), media.pause()))

    def load_wave():
        if not info["has_audio"]:
            return None
        return waveform(src, os.path.join(tmp, "wave.png"))
    run_in_thread(load_wave, lambda p, e: p and (setattr(timeline, "wave", cairo.ImageSurface.create_from_png(p)),
                                                  timeline.queue_draw()))
    group = Adw.PreferencesGroup()

    def playhead():
        return media.get_timestamp() / 1_000_000 if media.get_timestamp() else timeline.playhead

    if kind in ("trimVideo", "trimAudio"):
        start = spin("Start", 0, duration, 0.05, 0, 2)
        end = spin("End", 0, duration, 0.05, duration, 2)
        for row, which in ((start, "start"), (end, "end")):
            button = Gtk.Button(label="At playhead", valign=Gtk.Align.CENTER, css_classes=["flat"])
            button.connect("clicked", lambda _b, row=row: row.set_value(playhead()))
            row.add_suffix(button)
            group.add(row)
        syncing = {"on": False}

        def from_rows(*_):
            if syncing["on"]:
                return
            timeline.start, timeline.end = start.get_value(), max(end.get_value(), start.get_value() + 0.05)
            timeline.queue_draw()

        def from_timeline():
            syncing["on"] = True
            start.set_value(timeline.start)
            end.set_value(timeline.end)
            syncing["on"] = False
        start.connect("notify::value", from_rows)
        end.connect("notify::value", from_rows)
        timeline.on_change = from_timeline
        silence = Adw.SwitchRow(title="Remove silence at the ends",
                                subtitle="Pauses within the recording are kept")
        if kind == "trimAudio":
            group.add(silence)
        group.set_description("Drag the orange handles or set exact times.")

        def params():
            if end.get_value() - start.get_value() < 0.05:
                raise ValueError("The selection is too short")
            p = {"start": start.get_value(),
                 "end": end.get_value() if end.get_value() < duration - 0.01 else None}
            if kind == "trimAudio":
                p["strip_silence"] = silence.get_active()
            return p
    elif kind in ("splitVideo", "videoSnapshots"):
        timeline.mode = "marks"
        entry = Adw.EntryRow(title="Split points" if kind == "splitVideo" else "Frames to save")
        add = Gtk.Button(label="Add at playhead", valign=Gtk.Align.CENTER, css_classes=["flat"])

        def add_mark(*_):
            timeline.marks = sorted(set(timeline.marks + [round(playhead(), 3)]))
            entry.set_text(", ".join(fmt_time(t) for t in timeline.marks))
            timeline.queue_draw()
        add.connect("clicked", add_mark)
        entry.add_suffix(add)

        def from_entry(*_):
            try:
                timeline.marks = parse_times(entry.get_text())
                timeline.queue_draw()
            except ValueError:
                pass
        entry.connect("changed", from_entry)
        group.add(entry)
        every = spin("Or split every (seconds)", 0, max(1, duration), 1, 0, 0,
                     "0 uses the split points above") if kind == "splitVideo" else None
        if every:
            group.add(every)
        step = Adw.ActionRow(title="Step one frame")
        for label, delta in (("−", -1 / 30), ("+", 1 / 30)):
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, delta=delta: (media.pause(), media.seek(int(max(0, playhead() + delta) * 1e6)),
                                                          setattr(timeline, "playhead", max(0, playhead() + delta)),
                                                          timeline.queue_draw()))
            step.add_suffix(b)
        group.add(step)
        group.set_description("Times look like 1:05.5 or 65.5, separated by commas."
                              if kind == "splitVideo" else
                              "Pause on a frame and add it. Each frame saves as a full-resolution PNG.")

        def params():
            marks = parse_times(entry.get_text())
            if kind == "splitVideo":
                if every and every.get_value() > 0:
                    return {"every": every.get_value()}
                if not marks:
                    raise ValueError("Add at least one split point")
                return {"points": marks}
            return {"times": marks or [round(playhead(), 3)]}
    else:  # redactAudio
        timeline.mode = "ranges"
        listing = Adw.ActionRow(title="No ranges yet", subtitle="Drag across the waveform to mark speech to bleep")
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, css_classes=["flat"])
        clear.connect("clicked", lambda *_: (timeline.ranges.clear(), timeline.queue_draw(), timeline.on_change()))
        listing.add_suffix(clear)
        tone = combo("Bleep tone", ["1 kHz", "800 Hz", "440 Hz"], 0)

        def refresh():
            rs = [r for r in timeline.ranges if r[1] - r[0] > 0.02]
            listing.set_title(f"{len(rs)} range{'s' if len(rs) != 1 else ''}" if rs else "No ranges yet")
            if rs:
                listing.set_subtitle(", ".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in rs))
        timeline.on_change = refresh
        group.add(listing)
        group.add(tone)

        def params():
            rs = [tuple(r) for r in timeline.ranges if r[1] - r[0] > 0.02]
            if not rs:
                raise ValueError("Mark at least one range")
            return {"ranges": rs, "frequency": [1000, 800, 440][tone.get_selected()]}
    d.params = params
    d.set_body(_split(top, settings_page(group)))
    return d


class _StillMedia:
    """Stands in for Gtk.MediaFile when playback is unavailable."""

    def __init__(self):
        self.position = 0
        self.on_seek = lambda t: None

    def is_seekable(self):
        return True

    def seek(self, microseconds):
        self.position = microseconds
        self.on_seek(microseconds / 1_000_000)

    def get_timestamp(self):
        return self.position

    def get_playing(self):
        return False

    def pause(self):
        pass


# --- Photo editing ------------------------------------------------------------------------------------

def _edit_image(files, on_accept):
    src = files[0]
    d = ToolDialog("Edit Photo", on_accept, width=1000, height=660)
    tmp = d.temp_dir()
    small = preview_copy(src, tmp, 900)
    magick = B.require("magick", "ImageMagick")
    counter = {"n": 0}

    def render(params):
        counter["n"] += 1
        out = os.path.join(tmp, f"edit-{counter['n'] % 4}.png")
        subprocess.run([magick, small] + T.edit_image_args(params) + [out], check=True,
                       capture_output=True)
        return out
    preview = LivePreview(render)
    preview.set_filename(small)
    sliders = {
        "exposure": spin("Exposure", -60, 60, 1, 0), "contrast": spin("Contrast", -60, 60, 1, 0),
        "saturation": spin("Saturation", 0, 200, 1, 100), "warmth": spin("Warmth", -100, 100, 1, 0),
        "detail": spin("Detail", -50, 100, 1, 0, subtitle="Sharpen or soften"),
        "vignette": spin("Vignette", 0, 60, 1, 0),
    }
    mono = Adw.SwitchRow(title="Black &amp; white")
    rotate = {"angle": 0}
    adjust = Adw.PreferencesGroup(title="Adjust")
    for row in sliders.values():
        adjust.add(row)
    adjust.add(mono)
    transform = Adw.PreferencesGroup(title="Transform")
    rot = Adw.ActionRow(title="Rotate")
    for icon, delta in (("object-rotate-left-symbolic", -90), ("object-rotate-right-symbolic", 90)):
        b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b, delta=delta: (rotate.__setitem__("angle", (rotate["angle"] + delta) % 360),
                                                      changed()))
        rot.add_suffix(b)
    flip = Adw.SwitchRow(title="Mirror")
    transform.add(rot)
    transform.add(flip)
    reset = Gtk.Button(label="Reset", css_classes=["flat"], margin_top=6, halign=Gtk.Align.CENTER)

    def do_reset(*_):
        for key, row in sliders.items():
            row.set_value(100 if key == "saturation" else 0)
        mono.set_active(False)
        flip.set_active(False)
        rotate["angle"] = 0
        changed()
    reset.connect("clicked", do_reset)

    def params():
        p = {k: row.get_value() for k, row in sliders.items()}
        p.update(mono=mono.get_active(), flip=flip.get_active(), rotate=rotate["angle"])
        return p

    def changed(*_):
        preview.refresh(params())
    for row in sliders.values():
        row.connect("notify::value", changed)
    mono.connect("notify::active", changed)
    flip.connect("notify::active", changed)
    d.params = params
    page = settings_page(adjust, transform)
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.set_vexpand(True)
    side.append(page)
    side.append(reset)
    d.set_body(_split(preview, side))
    return d


# --- Annotate -------------------------------------------------------------------------------------------

class AnnotateCanvas(Gtk.DrawingArea):
    def __init__(self, png: str, full: tuple[int, int]):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.surface = cairo.ImageSurface.create_from_png(png)
        self.full_w, self.full_h = full
        self.shapes: list[dict] = []
        self.tool = "arrow"
        self.color = "#ff5a00"
        self.text = "Text"
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        self.add_controller(drag)

    def _frame(self):
        w, h = self.get_width(), self.get_height()
        s = min((w - 24) / self.full_w, (h - 24) / self.full_h)
        return (w - self.full_w * s) / 2, (h - self.full_h * s) / 2, s

    def _img(self, x, y):
        ox, oy, s = self._frame()
        return (x - ox) / s, (y - oy) / s

    def _draw(self, _a, cr, w, h):
        ox, oy, s = self._frame()
        cr.translate(ox, oy)
        cr.scale(s, s)
        cr.save()
        cr.scale(self.full_w / self.surface.get_width(), self.full_h / self.surface.get_height())
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        cr.restore()
        T.draw_annotations(cr, self.shapes, max(self.full_w, self.full_h))

    def _begin(self, _g, x, y):
        self.grab_focus()
        ix, iy = self._img(x, y)
        shape = {"kind": self.tool, "x0": ix, "y0": iy, "x1": ix, "y1": iy, "color": self.color}
        if self.tool == "pen":
            shape["points"] = [(ix, iy)]
        if self.tool == "text":
            shape["text"] = self.text or "Text"
        self.shapes.append(shape)
        self.queue_draw()

    def _update(self, gesture, dx, dy):
        if not self.shapes:
            return
        ok, sx, sy = gesture.get_start_point()
        ix, iy = self._img(sx + dx, sy + dy)
        shape = self.shapes[-1]
        if shape["kind"] in ("text", "number"):
            shape["x0"], shape["y0"] = ix, iy
        elif shape["kind"] == "pen":
            shape["points"].append((ix, iy))
        else:
            shape["x1"], shape["y1"] = ix, iy
        self.queue_draw()


def _annotate_image(files, on_accept):
    src = files[0]
    d = ToolDialog("Annotate Photo", on_accept, width=1040, height=700)
    canvas = AnnotateCanvas(preview_copy(src, d.temp_dir(), 1400), oriented_size(src))
    bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, halign=Gtk.Align.CENTER)
    group_leader = None
    for kind, icon, tip in (("arrow", "go-next-symbolic", "Arrow"), ("rect", "checkbox-symbolic", "Box"),
                            ("ellipse", "radio-symbolic", "Ellipse"),
                            ("highlight", "format-text-highlight-symbolic", "Highlight"),
                            ("pen", "document-edit-symbolic", "Pen"), ("text", "insert-text-symbolic", "Text"),
                            ("number", "zoom-original-symbolic", "Numbered callout")):
        b = Gtk.ToggleButton(tooltip_text=tip)
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if theme.has_icon(icon):
            b.set_icon_name(icon)
        else:
            b.set_label(tip)
        if group_leader:
            b.set_group(group_leader)
        else:
            group_leader = b
            b.set_active(True)
        b.connect("toggled", lambda btn, kind=kind: btn.get_active() and setattr(canvas, "tool", kind))
        bar.append(b)
    text = Gtk.Entry(text="Text", width_chars=12, tooltip_text="Text placed by the Text tool")
    text.connect("changed", lambda e: setattr(canvas, "text", e.get_text()))
    bar.append(text)
    color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
    rgba = Gdk.RGBA()
    rgba.parse("#ff5a00")
    color.set_rgba(rgba)
    color.connect("notify::rgba", lambda *_: setattr(canvas, "color", rgba_hex(color)))
    bar.append(color)
    undo = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Undo")
    undo.connect("clicked", lambda *_: canvas.shapes and (canvas.shapes.pop(), canvas.queue_draw()))
    bar.append(undo)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(bar)
    frame = Gtk.Box(css_classes=["satsuma-canvas"], vexpand=True)
    frame.append(canvas)
    box.append(frame)

    def params():
        if not canvas.shapes:
            raise ValueError("Add at least one annotation")
        return {"shapes": canvas.shapes}
    d.params = params
    d.set_body(box)
    return d


# --- Background, collage --------------------------------------------------------------------------------

def _background_controls(d, changed, include_image=True):
    kind = combo("Background", ["Color", "Gradient"] + (["Image"] if include_image else []), 0)
    color_r, color_b = color_row("Color", "#ffffff")
    grad_r, grad_b = color_row("Gradient end", "#ff5a00")
    image = {"path": None}
    img_row = file_row("Background image", d.parent_window, lambda p: (image.__setitem__("path", p), changed()))

    def visibility(*_):
        sel = kind.get_selected()
        grad_r.set_visible(sel == 1)
        color_r.set_visible(sel in (0, 1))
        img_row.set_visible(sel == 2)
        changed()
    kind.connect("notify::selected", visibility)
    color_b.connect("notify::rgba", lambda *_: changed())
    grad_b.connect("notify::rgba", lambda *_: changed())
    visibility()

    def values():
        sel = kind.get_selected()
        if sel == 1:
            return {"gradient": [rgba_hex(color_b), rgba_hex(grad_b)]}
        if sel == 2 and image["path"]:
            return {"background_image": image["path"]}
        return {"background": rgba_hex(color_b)}
    return [kind, color_r, grad_r, img_row], values


def _frame_image(files, on_accept):
    src = files[0]
    d = ToolDialog("Add Background", on_accept, width=1000, height=660)
    tmp = d.temp_dir()
    small = preview_copy(src, tmp, 800)
    preview = LivePreview(lambda p: T._tool_frameImage(B.Runner(), small, p, tempfile.mkdtemp(dir=tmp)))
    aspects = ["Original", "1:1", "4:5", "5:4", "16:9", "9:16", "4:3", "3:2"]
    aspect = combo("Aspect ratio", aspects, 1)
    padding = spin("Spacing", 0, 40, 1, 8, subtitle="Percent of the photo")
    radius = spin("Corner radius", 0, 30, 1, 4, subtitle="Percent of the photo")
    shadow = Adw.SwitchRow(title="Shadow", active=True)

    def params():
        p = {"aspect": aspects[aspect.get_selected()].lower(), "padding": padding.get_value(),
             "radius": radius.get_value(), "shadow": shadow.get_active()}
        p.update(bg_values())
        return p

    def changed(*_):
        try:
            preview.refresh(params())
        except NameError:
            pass
    rows, bg_values = _background_controls(d, changed)
    group = Adw.PreferencesGroup()
    for row in rows + [aspect, padding, radius, shadow]:
        group.add(row)
    for row in (aspect,):
        row.connect("notify::selected", changed)
    for row in (padding, radius):
        row.connect("notify::value", changed)
    shadow.connect("notify::active", changed)
    d.params = params
    d.set_body(_split(preview, settings_page(group)))
    changed()
    return d


class OrderList(Gtk.ListBox):
    """Reorderable list of files (selection order is the starting order)."""

    def __init__(self, files: list[str], on_change=lambda: None):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.files = list(files)
        self.on_change = on_change
        self._rebuild()

    def _rebuild(self):
        while (row := self.get_row_at_index(0)) is not None:
            self.remove(row)
        for i, path in enumerate(self.files):
            row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)),
                                subtitle=f"{i + 1}")
            for icon, delta, tip in (("go-up-symbolic", -1, "Move up"), ("go-down-symbolic", 1, "Move down")):
                b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text=tip,
                               sensitive=0 <= i + delta < len(self.files))
                b.connect("clicked", lambda _b, i=i, delta=delta: self.move(i, delta))
                row.add_suffix(b)
            self.append(row)

    def move(self, i, delta):
        j = i + delta
        self.files[i], self.files[j] = self.files[j], self.files[i]
        self._rebuild()
        self.on_change()


def _order_dialog(files, on_accept, tool):
    titles = {"mergePDF": ("Merge PDFs", "Merge"), "createPDF": ("Create PDF", "Create"),
              "joinVideos": ("Join Videos", "Join")}
    title, verb = titles[tool]
    d = ToolDialog(title, on_accept, accept_label=verb, width=440, height=520)
    order = OrderList(files)
    group = Adw.PreferencesGroup(title="Order", description={
        "mergePDF": "Pages are combined in this order.",
        "createPDF": "One page per image, in this order.",
        "joinVideos": "Clips play in this order and save as one MP4."}[tool])
    group.add(order)
    d.params = lambda: {"order": order.files}
    d.set_body(settings_page(group))
    return d


def _collage(files, on_accept):
    d = ToolDialog("Create Collage", on_accept, accept_label="Create", width=1040, height=700)
    tmp = d.temp_dir()
    smalls = {}
    for i, path in enumerate(files):
        sub = os.path.join(tmp, f"s{i}")
        os.makedirs(sub)
        smalls[path] = preview_copy(path, sub, 500)

    def render(p):
        p = dict(p, order=[smalls[f] for f in p["order"]], width=900, format="png")
        return T._tool_createCollage(B.Runner(), list(smalls.values()), p, tempfile.mkdtemp(dir=tmp))
    preview = LivePreview(render)
    layouts = ["Grid", "Row", "Column", "Featured"]
    layout = combo("Layout", layouts, 0)
    spacing = spin("Spacing", 0, 10, 0.5, 1.5, 1, "Percent of the width")
    radius = spin("Corner radius", 0, 10, 0.5, 1.5, 1, "Percent of the width")
    out_fmt = combo("Save as", ["JPG", "PNG"], 0)

    def params():
        p = {"layout": layouts[layout.get_selected()].lower(), "spacing": spacing.get_value(),
             "radius": radius.get_value(), "order": order.files,
             "format": ["jpg", "png"][out_fmt.get_selected()]}
        p.update(bg_values())
        return p

    def changed(*_):
        try:
            preview.refresh(params())
        except NameError:
            pass
    order = OrderList(files, changed)
    rows, bg_values = _background_controls(d, changed, include_image=False)
    group = Adw.PreferencesGroup()
    for row in [layout] + rows + [spacing, radius, out_fmt]:
        group.add(row)
    layout.connect("notify::selected", changed)
    spacing.connect("notify::value", changed)
    radius.connect("notify::value", changed)
    ordering = Adw.PreferencesGroup(title="Order")
    ordering.add(order)
    d.params = params
    d.set_body(_split(preview, settings_page(group, ordering), 360))
    changed()
    return d


# --- Organize PDF ------------------------------------------------------------------------------------------

def _organize_pdf(files, on_accept):
    src = files[0]
    count = T.pdf_page_count(src)
    if not count:
        raise ValueError("Could not read the PDF's pages")
    d = ToolDialog("Organize Pages", on_accept, width=900, height=660)
    tmp = d.temp_dir()
    pages = [[i, 0] for i in range(1, count + 1)]  # [source page, rotation]
    thumbs: dict[int, str] = {}
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=6,
                       min_children_per_line=2, row_spacing=12, column_spacing=12, margin_top=12,
                       margin_bottom=12, margin_start=12, margin_end=12, valign=Gtk.Align.START)

    def card(index, page, rotation):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["card"],
                      margin_top=2, margin_bottom=2)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, height_request=150, can_shrink=True,
                          margin_top=6, margin_start=6, margin_end=6)
        if page in thumbs:
            texture_path = thumbs[page]
            if rotation:
                rotated = os.path.join(tmp, f"r{page}-{rotation}.png")
                if not os.path.exists(rotated):
                    subprocess.run([B.tool("magick"), texture_path, "-rotate", str(rotation), rotated],
                                   capture_output=True)
                texture_path = rotated
            pic.set_filename(texture_path)
        box.append(pic)
        box.append(Gtk.Label(label=f"{index + 1} · page {page}", css_classes=["caption"]))
        actions = Gtk.Box(halign=Gtk.Align.CENTER, css_classes=["linked"], margin_bottom=6)
        for icon, tip, fn in (("go-previous-symbolic", "Move earlier", lambda: move(index, -1)),
                              ("object-rotate-right-symbolic", "Rotate", lambda: rotate(index)),
                              ("edit-copy-symbolic", "Duplicate", lambda: duplicate(index)),
                              ("user-trash-symbolic", "Remove", lambda: remove(index)),
                              ("go-next-symbolic", "Move later", lambda: move(index, 1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"])
            b.connect("clicked", lambda _b, fn=fn: fn())
            actions.append(b)
        box.append(actions)
        return box

    def rebuild():
        flow.remove_all()
        for i, (page, rotation) in enumerate(pages):
            flow.append(card(i, page, rotation))

    def move(i, delta):
        j = i + delta
        if 0 <= j < len(pages):
            pages[i], pages[j] = pages[j], pages[i]
            rebuild()

    def rotate(i):
        pages[i][1] = (pages[i][1] + 90) % 360
        rebuild()

    def duplicate(i):
        pages.insert(i + 1, list(pages[i]))
        rebuild()

    def remove(i):
        if len(pages) > 1:
            del pages[i]
            rebuild()

    def make_thumbs():
        B.rasterise_pdf(B.Runner(), src, "png", os.path.join(tmp, "t"), dpi=40)
        found = {}
        for name in os.listdir(tmp):
            if name.startswith("t-") and name.endswith(".png"):
                found[int(name[2:-4])] = os.path.join(tmp, name)
        return found
    run_in_thread(make_thumbs, lambda found, e: (thumbs.update(found or {}), rebuild()))
    rebuild()
    scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroller.set_child(flow)
    d.params = lambda: {"pages": [tuple(p) for p in pages]}
    d.set_body(scroller)
    return d


# --- Simple form dialogs -------------------------------------------------------------------------------------

def _compress(files, on_accept):
    cats = {F.category(F.detect_format(f)) for f in files}
    d = ToolDialog("Compress", on_accept, accept_label="Compress")
    groups, getters = [], []
    if "image" in cats:
        g = Adw.PreferencesGroup(title="Images")
        quality = spin("Quality", 10, 100, 1, 72, subtitle="Lower is smaller")
        side = combo("Resize", ["Keep size", "Longest side 3840 px", "2560 px", "1600 px", "1080 px"], 0)
        g.add(quality)
        g.add(side)
        groups.append(g)
        getters.append(lambda: {"quality": int(quality.get_value()),
                                "max_side": [0, 3840, 2560, 1600, 1080][side.get_selected()]})
    if "video" in cats:
        g = Adw.PreferencesGroup(title="Videos")
        q = combo("Quality", ["Smallest", "Balanced", "Better"], 1)
        res = combo("Resolution", ["Keep", "1080p", "720p", "480p"], 0)
        g.add(q)
        g.add(res)
        groups.append(g)
        getters.append(lambda: {"crf": [32, 28, 23][q.get_selected()],
                                "max_height": [0, 1080, 720, 480][res.get_selected()]})
    if "audio" in cats:
        g = Adw.PreferencesGroup(title="Audio", description="WAV and AIFF are saved as lossless FLAC.")
        rates = [64, 96, 128, 160, 192, 256]
        br = combo("Bitrate", [f"{r} kbps" for r in rates], 2)
        g.add(br)
        groups.append(g)
        getters.append(lambda: {"bitrate": rates[br.get_selected()]})
    if "pdf" in cats:
        g = Adw.PreferencesGroup(title="PDFs")
        presets = ["screen", "ebook", "printer"]
        preset = combo("Target", ["Smallest (screen)", "Balanced (e-book)", "High quality (print)"], 1)
        g.add(preset)
        groups.append(g)
        getters.append(lambda: {"preset": presets[preset.get_selected()]})

    def params():
        p = {}
        for get in getters:
            p.update(get())
        return p
    d.params = params
    d.set_body(settings_page(*groups))
    return d


def _current_tags(path: str, cat: str) -> dict:
    try:
        if cat in ("audio", "video"):
            data = T._ffprobe(path)
            tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
            return tags
        if B.tool("exiftool"):
            out = subprocess.run([B.tool("exiftool"), "-j", path], capture_output=True, text=True).stdout
            return json.loads(out)[0]
    except (ValueError, IndexError, OSError):
        pass
    return {}


def _metadata(files, on_accept):
    cats = {F.category(F.detect_format(f)) for f in files}
    d = ToolDialog("Edit Metadata", on_accept)
    group = Adw.PreferencesGroup()
    single_cat = next(iter(cats)) if len(cats) == 1 else None
    can_edit = len(files) == 1 and single_cat in T.META_FIELDS
    mode = combo("Action", ["Remove all metadata"] + (["Edit fields"] if can_edit else []), 0)
    group.add(mode)
    fields_group = Adw.PreferencesGroup(title="Fields")
    entries = {}
    location = Adw.SwitchRow(title="Remove location", active=True)
    if can_edit:
        current = _current_tags(files[0], single_cat)
        for key, title in T.META_FIELDS[single_cat]:
            entry = Adw.EntryRow(title=title, text=str(current.get(key, current.get(key.lower(), "")) or ""))
            entries[key] = entry
            fields_group.add(entry)
        if single_cat == "image":
            fields_group.add(location)
            gps = current.get("GPSPosition")
            location.set_subtitle(f"Currently {gps}" if gps else "No location stored")
    fields_group.set_visible(False)
    mode.connect("notify::selected", lambda *_: fields_group.set_visible(mode.get_selected() == 1))
    group.set_description("Removes camera, location, author and software tags. "
                          "Image orientation and color profile are kept.")

    def params():
        if mode.get_selected() == 0:
            return {"mode": "remove"}
        return {"mode": "edit", "fields": {k: e.get_text() for k, e in entries.items()},
                "remove_location": location.get_active()}
    d.params = params
    d.set_body(settings_page(group, fields_group))
    return d


def _simple_choice(title, label, options, values, key, default=0, description="", accept="Save"):
    def build(files, on_accept):
        d = ToolDialog(title, on_accept, accept_label=accept)
        row = combo(label, options, default)
        group = Adw.PreferencesGroup(description=description)
        group.add(row)
        d.params = lambda: {key: values[row.get_selected()]}
        d.set_body(settings_page(group))
        return d
    return build


def _visualizer(files, on_accept):
    d = ToolDialog("Audio Visualizer", on_accept, accept_label="Create")
    shapes = ["landscape", "portrait", "square"]
    shape = combo("Shape", ["Landscape 16:9", "Portrait 9:16", "Square"], 0)
    bg = combo("Background", ["Black", "Image"], 0)
    image = {"path": None}
    img_row = file_row("Image", d.parent_window, lambda p: image.__setitem__("path", p))
    img_row.set_visible(False)
    bg.connect("notify::selected", lambda *_: img_row.set_visible(bg.get_selected() == 1))
    color_r, color_b = color_row("Waveform color", "#ff5a00")
    group = Adw.PreferencesGroup(description="Creates an MP4 with a waveform that moves with the audio.")
    for row in (shape, bg, img_row, color_r):
        group.add(row)

    def params():
        p = {"aspect": shapes[shape.get_selected()], "color": rgba_hex(color_b)}
        if bg.get_selected() == 1:
            if not image["path"]:
                raise ValueError("Choose a background image")
            p["background_image"] = image["path"]
        return p
    d.params = params
    d.set_body(settings_page(group))
    return d


BUILDERS = {
    "compress": _compress,
    "removeMetadata": _metadata,
    "editImage": _edit_image,
    "annotateImage": _annotate_image,
    "frameImage": _frame_image,
    "cropImage": _crop_image,
    "redactImage": _redact_image,
    "normalizeAudio": _simple_choice(
        "Normalize Volume", "Target loudness",
        ["Voice & podcasts (−16 LUFS)", "Music streaming (−14 LUFS)", "Broadcast (−23 LUFS)"],
        [-16, -14, -23], "lufs", description="Evens out the volume of one recording or a selection."),
    "trimVideo": lambda f, a: _media_dialog(f, a, "trimVideo"),
    "trimAudio": lambda f, a: _media_dialog(f, a, "trimAudio"),
    "splitVideo": lambda f, a: _media_dialog(f, a, "splitVideo"),
    "videoSnapshots": lambda f, a: _media_dialog(f, a, "videoSnapshots"),
    "redactAudio": lambda f, a: _media_dialog(f, a, "redactAudio"),
    "cropVideo": lambda f, a: _video_region(f, a, False),
    "redactVideo": lambda f, a: _video_region(f, a, True),
    "changeVideoSpeed": _simple_choice(
        "Change Video Speed", "Speed",
        ["0.25×", "0.5×", "0.75×", "1.25×", "1.5×", "2×", "3×", "4×"],
        [0.25, 0.5, 0.75, 1.25, 1.5, 2, 3, 4], "speed", default=5,
        description="Audio keeps its pitch."),
    "audioChannels": _simple_choice(
        "Convert Audio Channels", "Channels", ["Mono", "Stereo"], [1, 2], "channels",
        description="Mono to stereo puts the same audio in both channels."),
    "audioToVideo": _visualizer,
    "mergePDF": lambda f, a: _order_dialog(f, a, "mergePDF"),
    "joinVideos": lambda f, a: _order_dialog(f, a, "joinVideos"),
    "createPDF": lambda f, a: _order_dialog(f, a, "createPDF"),
    "createCollage": _collage,
    "organizePDF": _organize_pdf,
}
