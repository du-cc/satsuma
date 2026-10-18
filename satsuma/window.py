"""Main window: a drop zone that turns into the wheel while you drag."""

from __future__ import annotations

import os

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk

from . import actions as A
from . import backends as B
from . import formats as F
from .jobs import Job
from .wheel import Wheel

MODES = ["conversions", "tools", "actions"]
MODE_LABELS = {"conversions": "Convert", "tools": "Tools", "actions": "Actions"}
MODE_KEYS = {"conversions": "Shift", "tools": "Alt", "actions": "Ctrl"}
DWELL_MS = 280


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Satsuma", default_width=480, default_height=620)
        self.app = app
        self.files: list[str] = []
        self.dragging = False
        self.sticky = False
        self._dwell = None
        self._last_drop_actions = None
        self.start_mode = None  # wheel requested on the command line
        self.add_css_class("satsuma-window")

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Satsuma", subtitle="")
        header.set_title_widget(self.title)
        menu = Gio.Menu()
        menu.append("Open Files…", "win.open")
        menu.append("Paste Files", "win.paste")
        menu.append("Show Shelf", "app.shelf")
        section = Gio.Menu()
        section.append("Preferences", "app.preferences")
        section.append("Keyboard Shortcuts", "app.shortcuts")
        section.append("About Satsuma", "app.about")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu", primary=True))
        open_button = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open files (Ctrl+O)",
                                 action_name="win.open")
        header.pack_start(open_button)
        view.add_top_bar(header)

        # Mode chips double as hover targets while dragging.
        self.chips = {}
        chip_box = Gtk.Box(css_classes=["linked", "satsuma-chips"], halign=Gtk.Align.CENTER,
                           margin_top=10)
        leader = None
        for mode in MODES:
            chip = Gtk.ToggleButton(css_classes=["satsuma-chip"])
            label = Gtk.Box(spacing=6)
            label.append(Gtk.Label(label=MODE_LABELS[mode]))
            label.append(Gtk.Label(label=MODE_KEYS[mode], css_classes=["satsuma-key", "caption"]))
            chip.set_child(label)
            if leader:
                chip.set_group(leader)
            else:
                leader = chip
            chip.connect("toggled", self._on_chip, mode)
            chip_box.append(chip)
            self.chips[mode] = chip

        self.wheel = Wheel(scale=app.settings["wheel_scale"], sound=app.settings["sound"])
        self.wheel.set_halign(Gtk.Align.CENTER)
        self.wheel.set_valign(Gtk.Align.CENTER)
        self.wheel.set_vexpand(True)
        self.wheel.connect("activated", self._on_activated)
        self.wheel.connect("mode-requested", lambda _w, mode: self.set_mode(mode))
        self.wheel.connect("cancelled", lambda *_: self.cancel())
        self.wheel.connect("selection-changed", lambda *_: self._update_hint())

        self.hint = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER,
                              margin_start=24, margin_end=24, margin_bottom=8)
        self.wheel_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.wheel_page.append(chip_box)
        self.wheel_page.append(self.wheel)
        self.wheel_page.append(self.hint)

        idle = Adw.StatusPage(icon_name="io.github.satsuma.Satsuma", title="Drop files here",
                              description="Drag files from Files onto this window and choose "
                                          "a format on the wheel.\nHold <b>Ctrl</b> for file actions or "
                                          "<b>Alt</b> for tools while dragging.",
                              vexpand=True)
        idle.add_css_class("satsuma-idle")
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        open_pill = Gtk.Button(label="Open Files…", css_classes=["pill", "suggested-action"],
                               action_name="win.open")
        buttons.append(open_pill)
        idle.set_child(buttons)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140)
        self.stack.add_named(idle, "idle")
        self.stack.add_named(self.wheel_page, "wheel")

        # Jobs list.
        self.jobs_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        jobs_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True, max_content_height=190)
        jobs_scroller.set_child(self.jobs_list)
        jobs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12,
                           margin_bottom=12, spacing=6)
        jobs_header = Gtk.Box()
        jobs_header.append(Gtk.Label(label="Recent", css_classes=["heading"], hexpand=True, xalign=0))
        clear = Gtk.Button(label="Clear", css_classes=["flat"])
        clear.connect("clicked", lambda *_: self._clear_finished())
        jobs_header.append(clear)
        jobs_box.append(jobs_header)
        jobs_box.append(jobs_scroller)
        self.jobs_revealer = Gtk.Revealer(child=jobs_box, reveal_child=False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.stack)
        content.append(self.jobs_revealer)
        self.toasts.set_child(content)
        view.set_content(self.toasts)
        self.set_content(view)

        # Drag and drop. LINK is preferred so the compositor reports Ctrl (COPY)
        # and Alt (ASK) as distinct actions; MOVE is never accepted, so the
        # source app can never delete what you dropped.
        self.drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.LINK
                                       | Gdk.DragAction.ASK)
        self.drop.set_preload(True)
        self.drop.connect("enter", self._on_enter)
        self.drop.connect("motion", self._on_motion)
        self.drop.connect("leave", self._on_leave)
        self.drop.connect("drop", self._on_drop)
        self.drop.connect("notify::value", self._on_value)
        content.add_controller(self.drop)
        self.content = content

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_window_key)
        self.add_controller(keys)

        for name, callback in (("open", self.open_files), ("paste", self.paste_files)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        app.set_accels_for_action("win.open", ["<Control>o"])
        app.set_accels_for_action("win.paste", ["<Control>v"])

        app.jobs.connect("job-added", self._on_job_added)
        app.jobs.connect("job-changed", self._on_job_changed)
        self._rows: dict[int, Adw.ActionRow] = {}
        self.set_mode(app.settings["default_wheel"], refresh=False)
        self._update_hint()

    # --- selection and modes ---------------------------------------------------------------

    def set_files(self, paths: list[str], *, sticky: bool = True, keep_mode: bool = False):
        self.files = [os.path.abspath(p) for p in paths if os.path.exists(p)]
        if not self.files:
            self.cancel()
            return
        self.sticky = sticky
        self.title.set_subtitle(os.path.basename(self.files[0]) if len(self.files) == 1
                                else f"{len(self.files)} items")
        formats = [F.detect_format(p) for p in self.files]
        # A fresh selection starts on the default wheel; a drag keeps the one in use.
        fresh = sticky and not keep_mode
        mode = (self.start_mode or self.app.settings["default_wheel"]) if fresh else self.wheel.mode
        self.start_mode = None
        if not self._targets(mode, formats):
            for candidate in MODES:
                if self._targets(candidate, formats):
                    mode = candidate
                    break
        self.set_mode(mode)
        self.stack.set_visible_child_name("wheel")
        if sticky:
            self.wheel.grab_focus()
            if self.wheel.items:
                self.wheel.select(0, sound=False)

    def _targets(self, mode, formats):
        if mode == "conversions":
            return F.selection_targets(formats)
        if mode == "tools":
            return F.tool_targets(formats)
        return F.action_targets()

    def set_mode(self, mode: str, refresh: bool = True):
        formats = [F.detect_format(p) for p in self.files]
        items = self._targets(mode, formats) if self.files else []
        disabled = set()
        if mode == "conversions":
            disabled = {t for t in items if not all(B.is_available(f, t) for f in formats)}
        keep = self.wheel.selected if mode == self.wheel.mode else None
        self.wheel.set_items(mode, items, disabled)
        if self.sticky and items and keep is None and refresh:
            self.wheel.select(next((i for i, k in enumerate(items) if k not in disabled), None), sound=False)
        if not self.chips[mode].get_active():
            self.chips[mode].set_active(True)
        self._update_hint()

    def _on_chip(self, chip, mode):
        if chip.get_active() and mode != self.wheel.mode:
            self.set_mode(mode)

    def _update_hint(self):
        key = self.wheel.selected_key()
        mode = self.wheel.mode
        if not self.files:
            text = ""
        elif key and key in self.wheel.disabled:
            text = "Install the missing converter in Preferences → Converters"
        elif key:
            verb = "Let go" if self.dragging else "Press Enter or click"
            text = f"{verb} to {F.item_phrase(mode, key)}"
        elif not self.wheel.items:
            text = ""
        elif self.dragging:
            text = "Drop onto an option. Hover the buttons above, or hold Ctrl or Alt, to switch wheels."
        else:
            text = "Use the arrow keys and Enter, or click. Shift, Alt and Ctrl switch wheels. Esc cancels."
        self.hint.set_label(text)

    def cancel(self):
        self.files = []
        self.sticky = False
        self.dragging = False
        self.title.set_subtitle("")
        self.wheel.set_items(self.wheel.mode, [])
        self.stack.set_visible_child_name("idle")

    # --- drag and drop ------------------------------------------------------------------------

    def _on_value(self, target, _pspec):
        value = target.get_value()
        if value is not None and self.dragging:
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            if paths:
                self.set_files(paths, sticky=False)

    def _on_enter(self, target, x, y):
        self.dragging = True
        self._last_drop_actions = None
        self._on_value(target, None)  # shows the wheel once the file list is known
        self._update_hint()
        return Gdk.DragAction.LINK

    def _wheel_point(self, x, y):
        ok, point = self.content.compute_point(self.wheel, Graphene.Point().init(x, y))
        return (point.x, point.y) if ok else (-1, -1)

    def _chip_at(self, x, y):
        for mode, chip in self.chips.items():
            ok, point = self.content.compute_point(chip, Graphene.Point().init(x, y))
            if ok and chip.contains(point.x, point.y):
                return mode
        return None

    def _modifier_mode(self):
        """Wheel requested by held modifiers, from the drop's negotiated action
        (Wayland) or the keyboard state (X11 / focused window)."""
        drop = self.drop.get_current_drop()
        if drop is not None:
            actions = drop.get_actions()
            if actions != self._last_drop_actions:
                self._last_drop_actions = actions
                if actions & Gdk.DragAction.ASK:
                    return "tools"
                if actions == Gdk.DragAction.COPY:
                    return "actions"
        seat = self.get_display().get_default_seat()
        keyboard = seat.get_keyboard() if seat else None
        if keyboard is not None:
            state = keyboard.get_modifier_state()
            if state & Gdk.ModifierType.ALT_MASK:
                return "tools"
            if state & Gdk.ModifierType.CONTROL_MASK:
                return "actions"
            if state & Gdk.ModifierType.SHIFT_MASK:
                return "conversions"
        return None

    def _on_motion(self, target, x, y):
        mode = self._modifier_mode()
        if mode and mode != self.wheel.mode:
            self.set_mode(mode)
        chip_mode = self._chip_at(x, y)
        if chip_mode and chip_mode != self.wheel.mode:
            if self._dwell is None or self._dwell[0] != chip_mode:
                if self._dwell:
                    GLib.source_remove(self._dwell[1])
                self._dwell = (chip_mode, GLib.timeout_add(DWELL_MS, self._dwell_fire, chip_mode))
        elif self._dwell:
            GLib.source_remove(self._dwell[1])
            self._dwell = None
        wx, wy = self._wheel_point(x, y)
        self.wheel.select(self.wheel.index_at(wx, wy))
        return Gdk.DragAction.LINK

    def _dwell_fire(self, mode):
        self._dwell = None
        self.set_mode(mode)
        return False

    def _on_leave(self, _target):
        if self._dwell:
            GLib.source_remove(self._dwell[1])
            self._dwell = None
        was_dragging = self.dragging
        self.dragging = False
        # Leave also fires right before a drop; only reset when nothing was dropped.
        if was_dragging:
            GLib.timeout_add(60, self._after_leave)

    def _after_leave(self):
        if not self.dragging and not self.sticky:
            self.cancel()
        return False

    def _on_drop(self, _target, value, x, y):
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            self.toast("Only local files can be converted")
            return False
        self.files = [os.path.abspath(p) for p in paths]
        wx, wy = self._wheel_point(x, y)
        index = self.wheel.index_at(wx, wy)
        self.dragging = False
        if index is not None and self.wheel.items:
            self.wheel.select(index, sound=False)
            if self.wheel.activate_selected():
                return True
        # Dropped in the middle or off the wheel: keep it open for clicking.
        self.set_files(self.files, sticky=True, keep_mode=True)
        self.present()
        return True

    # --- keyboard -------------------------------------------------------------------------------

    def _on_window_key(self, _ctl, keyval, _code, state):
        if self.stack.get_visible_child_name() == "wheel" and not self.wheel.has_focus():
            if keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Return,
                          Gdk.KEY_Escape, Gdk.KEY_Tab):
                self.wheel.grab_focus()
                return False
        return False

    # --- choosing files without dragging -----------------------------------------------------------

    def open_files(self):
        dialog = Gtk.FileDialog(title="Choose files to convert", modal=True)

        def done(dlg, result):
            try:
                files = dlg.open_multiple_finish(result)
            except GLib.Error:
                return
            self.set_files([f.get_path() for f in files if f.get_path()])
        dialog.open_multiple(self, None, done)

    def paste_files(self):
        clipboard = self.get_display().get_clipboard()

        def done(cb, result):
            try:
                value = cb.read_value_finish(result)
            except GLib.Error:
                self.toast("The clipboard has no files")
                return
            self.set_files([f.get_path() for f in value.get_files() if f.get_path()])
        clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, done)

    # --- acting on a choice ---------------------------------------------------------------------------

    def _on_activated(self, _wheel, mode, key):
        files = list(self.files)
        if not files:
            return
        self._reset_after_choice()
        self.app.perform(mode, key, files, self, self.toast)

    def _reset_after_choice(self):
        self.sticky = False
        self.files = []
        self.title.set_subtitle("")
        self.wheel.set_items(self.wheel.mode, [])
        self.stack.set_visible_child_name("idle")

    # --- jobs -------------------------------------------------------------------------------------------

    def toast(self, text, button=None, callback=None):
        toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=4)
        if button and callback:
            toast.set_button_label(button)
            toast.connect("button-clicked", lambda *_: callback())
        self.toasts.add_toast(toast)

    def _on_job_added(self, _queue, job: Job):
        row = Adw.ActionRow(title=GLib.markup_escape_text(job.title), subtitle="Waiting…")
        spinner = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        row.add_prefix(spinner)
        cancel = Gtk.Button(icon_name="process-stop-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat", "circular"], tooltip_text="Cancel")
        cancel.connect("clicked", lambda *_: job.cancel())
        row.add_suffix(cancel)
        row._spinner, row._cancel = spinner, cancel
        self._rows[id(job)] = row
        self.jobs_list.prepend(row)
        self.jobs_revealer.set_reveal_child(True)

    def _on_job_changed(self, _queue, job: Job):
        row = self._rows.get(id(job))
        if row is None:
            return
        if job.state == "running":
            row.set_subtitle("Working…")
            return
        row._spinner.set_spinning(False)
        row._spinner.set_visible(False)
        row.remove(row._cancel)
        if job.state == "done":
            names = [os.path.basename(p) for p in job.outputs]
            label = names[0] if len(names) == 1 else f"{len(names)} files"
            row.set_title(GLib.markup_escape_text(label))
            row.set_subtitle("Done — just like that")
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic", css_classes=["success"]))
            show = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                              css_classes=["flat", "circular"], tooltip_text="Show in Files")
            show.connect("clicked", lambda *_: show_in_files(job.outputs))
            row.add_suffix(show)
            for p in job.outputs:
                source = Gtk.DragSource(actions=Gdk.DragAction.COPY)
                source.connect("prepare", lambda *_: A.file_list_provider(job.outputs))
                row.add_controller(source)
                break
            if self.get_visible():
                self.toast(f"{label} — just like that.", "Show", lambda: show_in_files(job.outputs))
        elif job.state == "failed":
            row.set_subtitle(GLib.markup_escape_text(job.error.splitlines()[0][:160]))
            row.set_tooltip_text(job.error)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["error"]))
            if self.get_visible():
                self.toast(f"Couldn't make {job.title}")
        else:
            row.set_subtitle("Cancelled")
            row.add_prefix(Gtk.Image(icon_name="process-stop-symbolic", css_classes=["dim-label"]))

    def _clear_finished(self):
        for job in list(self.app.jobs.jobs):
            if job.state in ("done", "failed", "cancelled"):
                row = self._rows.pop(id(job), None)
                if row:
                    self.jobs_list.remove(row)
                self.app.jobs.jobs.remove(job)
        if not self._rows:
            self.jobs_revealer.set_reveal_child(False)


def show_in_files(paths: list[str]):
    uris = [Gio.File.new_for_path(p).get_uri() for p in paths]
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", (uris, "")), None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(paths[0])).get_uri(), None)
