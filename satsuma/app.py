"""Satsuma application: windows, preferences, notifications and GUI command line."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, VERSION  # noqa: E402
from . import actions as A  # noqa: E402
from . import backends as B  # noqa: E402
from . import dialogs as D  # noqa: E402
from . import integration  # noqa: E402
from .actions import Shelf  # noqa: E402
from .jobs import JobQueue  # noqa: E402
from .settings import Settings  # noqa: E402
from .window import Window, show_in_files  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = Settings()
        self.jobs = JobQueue(self.settings["parallel_jobs"])
        self.jobs.connect("job-added", lambda *_: self.hold())
        self.jobs.connect("job-changed", self._on_job_changed)
        # Started by D-Bus for the hotkey: stay warm for a while so the next
        # wheel opens instantly.
        self.set_inactivity_timeout(10 * 60 * 1000)
        self.add_main_option("wheel", ord("w"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the wheel overlay for the given files", None)
        self.add_main_option("window", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the drop-zone window with the given files", None)
        self.add_main_option("mode", ord("m"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Wheel to start on: conversions, tools or actions", "MODE")
        self.add_main_option("convert-to", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Convert the given files in the background", "FORMAT")
        self.add_main_option("shelf", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Put the given files on the shelf", None)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show the version", None)

    # --- startup ------------------------------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        icon_dir = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icon_dir):
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(icon_dir)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(DATA_DIR, "style.css"))
        # Above USER so a theme's ~/.config/gtk-4.0/gtk.css can't paint over the
        # transparent overlay; every rule targets Satsuma's own satsuma-* classes.
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        for name, callback in (("preferences", self.show_preferences), ("about", self.show_about),
                               ("shortcuts", self.show_shortcuts), ("shelf", lambda: Shelf.show(self, [])),
                               ("quit", self.quit), ("new-window", self.new_window)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        # Parameterised actions, also reachable over D-Bus (org.freedesktop.Application),
        # which is how the Files hotkey opens the wheel without starting a new process.
        for name, vtype, callback in (
                ("show-files", "as", lambda v: show_in_files(v.unpack())),
                ("wheel", "as", lambda v: self.open_wheel(_existing(v.unpack()))),
                ("wheel-mode", "(sas)", lambda v: self.open_wheel(_existing(v.unpack()[1]), v.unpack()[0])),
                ("convert", "(sas)", lambda v: self.convert_in_background(_existing(v.unpack()[1]),
                                                                         v.unpack()[0])),
                ("shelf-files", "as", lambda v: Shelf.show(self, _existing(v.unpack())))):
            action = Gio.SimpleAction.new(name, GLib.VariantType(vtype))
            action.connect("activate", lambda _a, value, cb=callback: cb(value))
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("app.new-window", ["<Control>n"])
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("version"):
            command_line.print_literal(f"Satsuma {VERSION}\n")
            return 0
        args = command_line.get_arguments()[1:]
        cwd = command_line.get_cwd() or os.getcwd()
        paths = []
        for arg in args:
            if arg.startswith("file://"):
                path = Gio.File.new_for_uri(arg).get_path()
            else:
                path = arg if os.path.isabs(arg) else os.path.join(cwd, arg)
            if path and os.path.exists(path):
                paths.append(os.path.normpath(path))
        mode = options.get("mode")
        if options.get("shelf"):
            Shelf.show(self, paths)
        elif options.get("convert-to") and paths:
            self.convert_in_background(paths, options["convert-to"])
        elif paths and options.get("window"):
            window = Window(self)
            window.start_mode = mode
            window.set_files(paths)
            window.present()
        elif paths:
            self.open_wheel(paths, mode)
        else:
            self.activate()
        return 0

    def do_activate(self):
        windows = [w for w in self.get_windows() if isinstance(w, Window)]
        (windows[0] if windows else Window(self)).present()

    def new_window(self):
        Window(self).present()

    def open_wheel(self, paths: list[str], mode: str | None = None):
        if not paths:
            return
        from .overlay import Overlay
        for window in self.get_windows():
            if isinstance(window, Overlay):
                window.dismiss()
        Overlay(self, paths, mode if mode in ("conversions", "tools", "actions") else None).popup()

    # --- doing the chosen thing -------------------------------------------------------------------

    def perform(self, mode: str, key: str, files: list[str], parent: Gtk.Widget | None, toast=None):
        """Run a wheel choice. ``parent`` is None when the wheel was the overlay."""
        toast = toast or self.toast
        if mode == "conversions":
            self.jobs.convert(files, key, self.settings.output_dir())
        elif mode == "tools":
            def accept(params):
                self.jobs.tool(key, files, params, self.settings.output_dir())
            if not D.open_tool_dialog(parent, key, files, accept):
                self.jobs.tool(key, files, {}, self.settings.output_dir())
        else:
            A.run(key, files, parent, toast, self)

    def toast(self, text: str):
        """Feedback for actions started from the overlay, where there is no window."""
        windows = [w for w in self.get_windows() if isinstance(w, Window) and w.get_visible()]
        if windows:
            windows[0].toast(text)

    def convert_in_background(self, paths, target):
        """Convert from the Files menu without a window; notifications report back."""
        self.jobs.convert(paths, target, self.settings.output_dir())

    # --- jobs ---------------------------------------------------------------------------------

    def _on_job_changed(self, _queue, job):
        if job.state in ("queued", "running"):
            return
        if job.state == "done":
            names = [os.path.basename(p) for p in job.outputs]
            self.notify_done(job, names[0] if len(names) == 1 else f"{len(names)} files")
        elif job.state == "failed":
            self.notify_failed(job)
        self.release()

    def notify_done(self, job, label):
        if not self.settings["notifications"] or self._window_focused():
            return
        note = Gio.Notification.new(f"{label} — just like that.")
        note.set_body("Saved next to the original" if not self.settings.output_dir()
                      else f"Saved in {self.settings.output_dir()}")
        note.set_default_action_and_target("app.show-files", GLib.Variant("as", job.outputs))
        note.add_button_with_target("Show in Files", "app.show-files", GLib.Variant("as", job.outputs))
        self.send_notification(None, note)

    def notify_failed(self, job):
        if self._window_focused():
            return  # the window shows the error in its job list
        note = Gio.Notification.new(f"Couldn't make {job.title}")
        note.set_body(job.error.splitlines()[0][:200] if job.error else "")
        self.send_notification(None, note)

    def _window_focused(self):
        return any(isinstance(w, Window) and w.is_active() and w.get_visible() for w in self.get_windows())

    # --- dialogs ---------------------------------------------------------------------------------

    def _parent(self):
        return self.get_active_window()

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Satsuma", application_icon=APP_ID, version=VERSION,
            developer_name="Satsuma contributors", license_type=Gtk.License.MIT_X11,
            comments="Select a file, press the shortcut, pick a format on the wheel. "
                     "Conversions run locally with FFmpeg, ImageMagick, LibreOffice, Pandoc and Poppler.",
            website="https://github.com/")
        about.present(self._parent())

    def show_shortcuts(self):
        builder = Gtk.Builder.new_from_string(SHORTCUTS_UI, -1)
        window = builder.get_object("shortcuts")
        window.set_transient_for(self._parent())
        window.present()

    def show_preferences(self):
        PreferencesDialog(self).present(self._parent())


SHORTCUTS_UI = """
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts">
    <property name="modal">1</property>
    <child><object class="GtkShortcutsSection"><property name="section-name">main</property>
      <child><object class="GtkShortcutsGroup"><property name="title">While dragging onto Satsuma</property>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Control_L</property>
          <property name="title">File actions wheel</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Alt_L</property>
          <property name="title">File tools wheel</property></object></child>
      </object></child>
      <child><object class="GtkShortcutsGroup"><property name="title">Wheel</property>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Shift_L</property>
          <property name="title">Conversions</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Alt_L</property>
          <property name="title">Tools</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Control_L</property>
          <property name="title">Actions</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Tab</property>
          <property name="title">Next wheel</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Left Right</property>
          <property name="title">Move between options</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Return</property>
          <property name="title">Apply</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">Escape</property>
          <property name="title">Cancel</property></object></child>
      </object></child>
      <child><object class="GtkShortcutsGroup"><property name="title">General</property>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;o</property>
          <property name="title">Open files</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;v</property>
          <property name="title">Paste files copied in Files</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">F4</property>
          <property name="title">Open the wheel for the selection in Files (configurable)</property></object></child>
        <child><object class="GtkShortcutsShortcut"><property name="accelerator">&lt;Control&gt;comma</property>
          <property name="title">Preferences</property></object></child>
      </object></child>
    </object></child>
  </object>
</interface>
"""


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app: App):
        super().__init__(title="Preferences")
        self.app = app
        s = app.settings

        general = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        output = Adw.PreferencesGroup(title="Saving")
        self.out_row = Adw.ActionRow(title="Save new files", activatable=True)
        self.out_row.add_suffix(Gtk.Image(icon_name="folder-open-symbolic"))
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Save next to the original")
        reset.connect("clicked", lambda *_: self._set_output(""))
        self.out_row.add_suffix(reset)
        self.out_row.connect("activated", self._choose_output)
        self._set_output(s["output_dir"], save=False)
        output.add(self.out_row)
        general.add(output)

        wheel = Adw.PreferencesGroup(title="Wheel")
        modes = ["conversions", "tools", "actions"]
        default = Adw.ComboRow(title="Start on", model=Gtk.StringList.new(["Conversions", "Tools", "Actions"]),
                               selected=modes.index(s["default_wheel"]))
        default.connect("notify::selected", lambda r, _p: s.__setitem__("default_wheel", modes[r.get_selected()]))
        wheel.add(default)
        sizes = [1.0, 1.15, 1.35]
        size = Adw.ComboRow(title="Size", model=Gtk.StringList.new(["Compact", "Default", "Large"]),
                            selected=min(range(3), key=lambda i: abs(sizes[i] - s["wheel_scale"])))
        size.connect("notify::selected", lambda r, _p: self._set_scale(sizes[r.get_selected()]))
        wheel.add(size)
        for key, title, subtitle in (
                ("sound", "Highlight sound", "A soft tick as you move between options"),
                ("close_after_action", "Close after choosing",
                 "For wheels opened from Files or the command line")):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=s[key])
            row.connect("notify::active", lambda r, _p, key=key: s.__setitem__(key, r.get_active()))
            wheel.add(row)
        general.add(wheel)

        other = Adw.PreferencesGroup(title="Jobs")
        notes = Adw.SwitchRow(title="Notify when done", subtitle="When Satsuma is in the background",
                              active=s["notifications"])
        notes.connect("notify::active", lambda r, _p: s.__setitem__("notifications", r.get_active()))
        other.add(notes)
        parallel = Adw.SpinRow.new_with_range(1, 8, 1)
        parallel.set_title("Parallel jobs")
        parallel.set_subtitle("Applies after restarting Satsuma")
        parallel.set_value(s["parallel_jobs"])
        parallel.connect("notify::value", lambda r, _p: s.__setitem__("parallel_jobs", int(r.get_value())))
        other.add(parallel)
        general.add(other)
        self.add(general)

        self.add(self._converters_page())
        self.add(self._integration_page())

    def _set_scale(self, value):
        self.app.settings["wheel_scale"] = value
        for window in self.app.get_windows():
            if isinstance(window, Window):
                window.wheel.scale = value
                window.wheel.queue_resize()

    def _set_output(self, path, save=True):
        if save:
            self.app.settings["output_dir"] = path
        self.out_row.set_subtitle(GLib.markup_escape_text(path) if path else "Next to the original")

    def _choose_output(self, *_):
        dialog = Gtk.FileDialog(title="Save new files in")

        def done(dlg, result):
            try:
                self._set_output(dlg.select_folder_finish(result).get_path())
            except GLib.Error:
                pass
        dialog.select_folder(self.app.get_active_window(), None, done)

    def _converters_page(self):
        page = Adw.PreferencesPage(title="Converters", icon_name="application-x-executable-symbolic")
        rows = B.readiness()
        missing = [r for r in rows if not r["path"]]
        group = Adw.PreferencesGroup(
            title="Converters",
            description="Satsuma uses standard Fedora tools and never uploads files. "
                        "Options whose converter is missing are dimmed on the wheel.")
        for r in rows:
            row = Adw.ActionRow(title=r["name"], subtitle=GLib.markup_escape_text(r["purpose"]))
            ok = bool(r["path"])
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic" if ok else "dialog-warning-symbolic",
                                     css_classes=["success" if ok else "warning"]))
            if not ok:
                row.add_suffix(Gtk.Label(label=r["package"], css_classes=["dim-label", "caption"],
                                         wrap=True, max_width_chars=22, xalign=1))
            group.add(row)
        page.add(group)
        if missing:
            fix = Adw.PreferencesGroup(title="Install what's missing")
            free = [p for r in missing if "RPM Fusion" not in r["package"] and "openh264" not in r["package"]
                    for p in r["package"].split()]
            command = "sudo dnf install " + " ".join(dict.fromkeys(free)) if free else ""
            if command:
                row = Adw.ActionRow(title="Fedora packages", subtitle=GLib.markup_escape_text(command),
                                    subtitle_selectable=True)
                copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                  tooltip_text="Copy command")
                copy.connect("clicked", lambda *_: (Gdk.Display.get_default().get_clipboard().set(command),
                                                    self.add_toast(Adw.Toast(title="Command copied"))))
                row.add_suffix(copy)
                fix.add(row)
            if any("RPM Fusion" in r["package"] or "openh264" in r["package"] for r in missing):
                fix.add(Adw.ActionRow(
                    title="Patent-encumbered codecs",
                    subtitle="For HEIC photos and H.264 video, enable RPM Fusion and run "
                             "sudo dnf swap ffmpeg-free ffmpeg --allowerasing; "
                             "sudo dnf install libheif-freeworld",
                    subtitle_selectable=True))
            page.add(fix)
        return page

    def _integration_page(self):
        page = Adw.PreferencesPage(title="Files", icon_name="system-file-manager-symbolic")
        group = Adw.PreferencesGroup(
            title="Files integration",
            description="Select files in Files and press the shortcut: the wheel pops up over "
                        "your screen at the pointer. Right-click also gets a “Satsuma” menu.")
        status = integration.status()
        rows = [("Wheel shortcut script", status["script"], ""),
                ("Open at the pointer (GNOME Shell extension)", status["shell_extension"],
                 "" if status["shell_extension"] else "Install, then log out and back in once"),
                ("Instant start (D-Bus activation)", status["service"], ""),
                ("Context menu", status["extension"],
                 "" if status["nautilus_python"] else "Needs nautilus-python (sudo dnf install nautilus-python)")]
        for title, ok, note in rows:
            row = Adw.ActionRow(title=title, subtitle=note or ("Installed" if ok else "Not installed"))
            row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic" if ok else "list-add-symbolic"))
            group.add(row)
        page.add(group)

        keys = Adw.PreferencesGroup(title="Shortcut",
                                    description="GTK syntax, for example F4, &lt;Shift&gt;space or "
                                                "&lt;Control&gt;&lt;Alt&gt;c. Applies after restarting Files.")
        accel = Adw.EntryRow(title="Open the wheel in Files", text=status["accel"] or integration.DEFAULT_ACCEL,
                             show_apply_button=True)

        def apply(row):
            value = row.get_text().strip()
            ok, _key, _mods = Gtk.accelerator_parse(value)
            if not ok:
                self.add_toast(Adw.Toast(title="That isn't a valid shortcut"))
                return
            integration.set_accel(value)
            self.add_toast(Adw.Toast(title="Saved — restart Files with “nautilus -q”"))
        accel.connect("apply", apply)
        keys.add(accel)
        page.add(keys)

        button = Gtk.Button(label="Install or Repair", css_classes=["pill", "suggested-action"],
                            halign=Gtk.Align.CENTER, margin_top=12)

        def do_install(*_):
            try:
                integration.install(accel.get_text().strip() or None)
                self.add_toast(Adw.Toast(title="Installed — restart Files with “nautilus -q”"))
            except OSError as exc:
                self.add_toast(Adw.Toast(title=f"Could not install: {exc}"))
        button.connect("clicked", do_install)
        install_group = Adw.PreferencesGroup()
        install_group.add(button)
        page.add(install_group)
        return page


def _existing(paths) -> list[str]:
    return [os.path.normpath(p) for p in paths if p and os.path.exists(p)]


def run_gui(argv=None) -> int:
    app = App()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(run_gui())
