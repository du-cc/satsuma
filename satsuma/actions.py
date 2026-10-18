"""File actions: Share, Float (shelf), Copy, Move, Copy Path, Terminal."""

from __future__ import annotations

import os
import shutil
import subprocess

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import formats as F


def _files(paths):
    return [Gio.File.new_for_path(p) for p in paths]


def file_list_provider(paths: list[str]) -> Gdk.ContentProvider:
    """Clipboard/drag content Files, browsers and editors all understand."""
    uris = [Gio.File.new_for_path(p).get_uri() for p in paths]
    providers = [
        Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(_files(paths))),
        Gdk.ContentProvider.new_for_bytes(
            "x-special/gnome-copied-files",
            GLib.Bytes.new(("copy\n" + "\n".join(uris)).encode())),
        Gdk.ContentProvider.new_for_bytes(
            "text/uri-list", GLib.Bytes.new(("\r\n".join(uris) + "\r\n").encode())),
    ]
    return Gdk.ContentProvider.new_union(providers)


def run(action: str, paths: list[str], window: Gtk.Window | None, toast, app) -> None:
    """Run a file action. ``window`` is None when chosen from the overlay."""
    {"share": share, "float": float_files, "copy": copy_files, "move": move_files,
     "copyPath": copy_path, "openTerminal": open_terminal}[action](paths, window, toast, app)


# --- Copy / Copy Path -------------------------------------------------------------------------

def _own_clipboard(app, set_content):
    """Set the clipboard and keep Satsuma running while it owns the content.

    Wayland clipboards are served by the owning process, so quitting right
    after the overlay closes would empty the clipboard.
    """
    clipboard = Gdk.Display.get_default().get_clipboard()
    set_content(clipboard)
    if app is None or not clipboard.is_local():
        return
    app.hold()

    def changed(cb, _pspec):
        if not cb.is_local():
            cb.disconnect(handler)
            app.release()
    handler = clipboard.connect("notify::local", changed)


def copy_files(paths, window, toast, app=None):
    _own_clipboard(app, lambda cb: cb.set_content(file_list_provider(paths)))
    toast(f"Copied {_count(paths)} — paste in Files")


def copy_path(paths, window, toast, app=None):
    text = "\n".join(os.path.abspath(p) for p in paths)
    _own_clipboard(app, lambda cb: cb.set(text))
    toast("Path copied" if len(paths) == 1 else f"{len(paths)} paths copied")


def _count(paths):
    return os.path.basename(paths[0]) if len(paths) == 1 else f"{len(paths)} items"


# --- Move ----------------------------------------------------------------------------------------

def move_files(paths, window, toast, app=None):
    dialog = Gtk.FileDialog(title="Move to…", modal=True)
    if app is not None:
        app.hold()  # keep running while the folder chooser is open
    dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(os.path.abspath(paths[0]))))

    def done(dlg, result):
        if app is not None:
            app.release()
        try:
            folder = dlg.select_folder_finish(result)
        except GLib.Error:
            return
        dest_dir = folder.get_path()
        moved = 0
        for path in paths:
            name = os.path.basename(path.rstrip(os.sep))
            if os.path.dirname(os.path.abspath(path)) == dest_dir:
                continue
            base, ext = (F.stem(name), name[len(F.stem(name)):].lstrip(".")) \
                if os.path.isfile(path) else (name, "")
            target = F.unique_path(dest_dir, base, ext)
            try:
                shutil.move(path, target)
                moved += 1
            except OSError as exc:
                toast(f"Could not move {name}: {exc.strerror or exc}")
                return
        toast(f"Moved {moved} item{'s' if moved != 1 else ''} to {os.path.basename(dest_dir) or dest_dir}")

    dialog.select_folder(window, None, done)


# --- Terminal -------------------------------------------------------------------------------------

TERMINALS = [
    ("ptyxis", ["--new-window", "--working-directory={dir}"]),
    ("kgx", ["--working-directory={dir}"]),
    ("gnome-terminal", ["--working-directory={dir}"]),
    ("konsole", ["--workdir", "{dir}"]),
    ("xfce4-terminal", ["--working-directory={dir}"]),
    ("tilix", ["--working-directory={dir}"]),
    ("alacritty", ["--working-directory", "{dir}"]),
    ("kitty", ["--directory", "{dir}"]),
    ("wezterm", ["start", "--cwd", "{dir}"]),
    ("xdg-terminal-exec", []),
    ("xterm", []),
]


def open_terminal(paths, window, toast, app=None):
    path = os.path.abspath(paths[0])
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    for exe, args in TERMINALS:
        found = shutil.which(exe)
        if found:
            subprocess.Popen([found] + [a.format(dir=directory) for a in args], cwd=directory,
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    toast("No terminal app found — install Ptyxis or GNOME Console")


# --- Share --------------------------------------------------------------------------------------------

def _gsconnect_devices():
    """(name, object_path) for paired, connected GSConnect devices."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.gnome.Shell.Extensions.GSConnect",
                              "/org/gnome/Shell/Extensions/GSConnect",
                              "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                              None, GLib.VariantType("(a{oa{sa{sv}}})"),
                              Gio.DBusCallFlags.NONE, 1500, None)
    except GLib.Error:
        return []
    devices = []
    for path, ifaces in reply.unpack()[0].items():
        props = ifaces.get("org.gnome.Shell.Extensions.GSConnect.Device")
        if props and props.get("Connected") and props.get("Paired"):
            devices.append((props.get("Name", "Phone"), path))
    return devices


def _gsconnect_share(path, uris):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for uri in uris:
        bus.call_sync("org.gnome.Shell.Extensions.GSConnect", path, "org.gtk.Actions", "Activate",
                      GLib.Variant("(sava{sv})", ("shareFile", [GLib.Variant("(sb)", (uri, False))], {})),
                      None, Gio.DBusCallFlags.NONE, 3000, None)


def _kdeconnect_devices():
    exe = shutil.which("kdeconnect-cli")
    if not exe:
        return []
    out = subprocess.run([exe, "-a", "--id-name-only"], capture_output=True, text=True).stdout
    devices = []
    for line in out.splitlines():
        if " " in line:
            device_id, name = line.split(" ", 1)
            devices.append((name, device_id))
    return devices


def share(paths, window, toast, app=None):
    files = [p for p in paths if os.path.isfile(p)]
    if not files:
        toast("Only files can be shared")
        return
    uris = [Gio.File.new_for_path(p).get_uri() for p in files]
    options = []
    for name, obj in _gsconnect_devices():
        options.append((f"{name}", "phone-symbolic", "GSConnect",
                        lambda obj=obj: _gsconnect_share(obj, uris)))
    for name, dev in _kdeconnect_devices():
        options.append((name, "phone-symbolic", "KDE Connect",
                        lambda dev=dev: [subprocess.Popen(["kdeconnect-cli", "-d", dev, "--share", u])
                                         for u in uris]))
    if shutil.which("bluetooth-sendto"):
        options.append(("Bluetooth", "bluetooth-active-symbolic", "Send to a nearby device",
                        lambda: subprocess.Popen(["bluetooth-sendto", *files], start_new_session=True)))
    if shutil.which("xdg-email"):
        args = ["xdg-email"]
        for f in files:
            args += ["--attach", f]
        options.append(("Email", "mail-send-symbolic", "Attach to a new message",
                        lambda: subprocess.Popen(args, start_new_session=True)))
    options.append(("Open With…", "view-more-symbolic", "Choose any app",
                    lambda: _open_with(files[0], window)))

    dialog = Adw.Dialog(title="Share", content_width=360)
    view = Adw.ToolbarView()
    view.add_top_bar(Adw.HeaderBar())
    group = Adw.PreferencesGroup(title=_count(files), margin_top=6, margin_bottom=18,
                                 margin_start=12, margin_end=12)
    for title, icon, subtitle, callback in options:
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
        row.add_prefix(Gtk.Image(icon_name=icon))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

        def activate(_row, callback=callback, title=title):
            dialog.close()
            try:
                callback()
                toast(f"Sharing with {title}")
            except (GLib.Error, OSError) as exc:
                toast(f"Could not share: {exc}")
        row.connect("activated", activate)
        group.add(row)
    view.set_content(group)
    if window is None:  # from the overlay: a small standalone window
        host = Adw.Window(title="Share", application=app, default_width=360, content=view, modal=False)
        dialog.close = host.close
        host.present()
        return
    dialog.set_child(view)
    dialog.present(window)


def _open_with(path, window):
    launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(path))
    if hasattr(launcher, "set_always_ask"):
        launcher.set_always_ask(True)
    launcher.launch(window, None, None)


# --- Float: the shelf ----------------------------------------------------------------------------------

class Shelf(Adw.Window):
    """A small window holding files you can drag into other apps."""

    _instance = None

    def __init__(self, app):
        super().__init__(application=app, title="Shelf", default_width=280, default_height=360)
        self.paths: list[str] = []
        header = Adw.HeaderBar()
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Clear shelf")
        clear.connect("clicked", lambda *_: self.set_paths([]))
        header.pack_end(clear)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.handle = Gtk.Button(css_classes=["pill", "suggested-action"], margin_top=6,
                                 margin_bottom=6, label="Drag all")
        self._make_draggable(self.handle, lambda: self.paths)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=10,
                      margin_end=10, margin_bottom=10)
        box.append(self.handle)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        box.append(scroller)
        self.empty = Adw.StatusPage(icon_name="view-pin-symbolic", title="Shelf is empty",
                                    description="Drop files here to keep them handy",
                                    css_classes=["compact"])
        self.stack = Gtk.Stack()
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(box, "files")
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.stack)
        self.set_content(view)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: self.add_paths(
            [f.get_path() for f in value.get_files() if f.get_path()]) or True)
        self.add_controller(drop)
        self.connect("close-request", self._on_close)

    @classmethod
    def show(cls, app, paths):
        if cls._instance is None:
            cls._instance = Shelf(app)
        cls._instance.add_paths(paths)
        cls._instance.present()
        return cls._instance

    def _on_close(self, *_):
        Shelf._instance = None
        return False

    def add_paths(self, paths):
        self.set_paths(self.paths + [p for p in paths if p not in self.paths])

    def set_paths(self, paths):
        self.paths = list(paths)
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for path in self.paths:
            self.list.append(self._row(path))
        self.handle.set_label("Drag file" if len(self.paths) == 1 else f"Drag all {len(self.paths)}")
        self.stack.set_visible_child_name("files" if self.paths else "empty")

    def _row(self, path):
        row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path.rstrip(os.sep))),
                            subtitle=GLib.markup_escape_text(os.path.dirname(path)))
        info = Gio.File.new_for_path(path).query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None) \
            if os.path.exists(path) else None
        row.add_prefix(Gtk.Image(gicon=info.get_icon() if info else Gio.ThemedIcon.new("text-x-generic"),
                                 pixel_size=32))
        remove = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat", "circular"], tooltip_text="Remove from shelf")
        remove.connect("clicked", lambda *_: self.set_paths([p for p in self.paths if p != path]))
        row.add_suffix(remove)
        self._make_draggable(row, lambda: [path])
        return row

    @staticmethod
    def _make_draggable(widget, get_paths):
        source = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        source.connect("prepare", lambda *_: file_list_provider(get_paths()) if get_paths() else None)
        widget.add_controller(source)


def float_files(paths, window, toast, app=None):
    Shelf.show(app or window.get_application(), paths)


GObject.type_ensure(Shelf)
