"""Satsuma menu for GNOME Files (nautilus-python, Nautilus 43 or newer).

Installed by Satsuma into ~/.local/share/nautilus-python/extensions/.
"""

import shlex
import subprocess
import sys

import gi

gi.require_version("Nautilus", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Nautilus  # noqa: E402

SATSUMA = "@SATSUMA_COMMAND@"
SATSUMA_LIB = "@SATSUMA_LIB@"
APP_ID = "@APP_ID@"
OBJECT_PATH = "@OBJECT_PATH@"

if SATSUMA_LIB not in sys.path:
    sys.path.insert(0, SATSUMA_LIB)
try:
    from satsuma import formats as F  # noqa: E402
except ImportError:  # Satsuma moved or was removed; keep Files working.
    F = None


def _activation_token():
    """A token that lets the wheel take keyboard focus from Files on Wayland."""
    try:
        context = Gdk.Display.get_default().get_app_launch_context()
        return context.get_startup_notify_id(None, []) or ""
    except (AttributeError, GLib.Error, TypeError):
        return ""


def _activate(action, parameter, fallback_args):
    token = _activation_token()
    data = {"activation-token": GLib.Variant("s", token),
            "desktop-startup-id": GLib.Variant("s", token)} if token else {}
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(APP_ID, OBJECT_PATH, "org.freedesktop.Application", "ActivateAction",
                 GLib.Variant("(sava{sv})", (action, [parameter], data)), None,
                 Gio.DBusCallFlags.NONE, 10000, None,
                 lambda b, r: _finish(b, r, fallback_args, token))
    except GLib.Error:
        _launch(fallback_args, token)


def _finish(bus, result, fallback_args, token):
    try:
        bus.call_finish(result)
    except GLib.Error:
        _launch(fallback_args, token)


def _launch(args, token=""):
    env = None
    if token:
        import os
        env = dict(os.environ, XDG_ACTIVATION_TOKEN=token)
    subprocess.Popen([*shlex.split(SATSUMA), *args], start_new_session=True, env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _paths_variant(paths):
    return GLib.Variant("as", paths)


class SatsumaMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        paths = [f.get_location().get_path() for f in files]
        if not paths or any(p is None for p in paths):
            return []
        top = Nautilus.MenuItem(name="Satsuma::top", label="Satsuma")
        menu = Nautilus.Menu()
        top.set_submenu(menu)

        wheel = Nautilus.MenuItem(name="Satsuma::wheel", label="Open Wheel")
        wheel.connect("activate", lambda *_: _activate("wheel", _paths_variant(paths), ["--wheel", *paths]))
        menu.append_item(wheel)

        if F is not None:
            formats = [F.detect_format(p) for p in paths]
            targets = F.selection_targets(formats)
            if targets:
                convert = Nautilus.MenuItem(name="Satsuma::convert", label="Convert To")
                sub = Nautilus.Menu()
                convert.set_submenu(sub)
                for target in targets:
                    item = Nautilus.MenuItem(name=f"Satsuma::to-{target}", label=F.label(target))
                    item.connect("activate", lambda _i, t=target: _activate(
                        "convert", GLib.Variant("(sas)", (t, paths)), ["--convert-to", t, *paths]))
                    sub.append_item(item)
                menu.append_item(convert)
            if F.tool_targets(formats):
                tools = Nautilus.MenuItem(name="Satsuma::tools", label="Tools")
                tools.connect("activate", lambda *_: _activate(
                    "wheel-mode", GLib.Variant("(sas)", ("tools", paths)), ["--wheel", "--mode", "tools", *paths]))
                menu.append_item(tools)

        shelf = Nautilus.MenuItem(name="Satsuma::shelf", label="Float on Shelf")
        shelf.connect("activate", lambda *_: _activate("shelf-files", _paths_variant(paths), ["--shelf", *paths]))
        menu.append_item(shelf)
        return [top]

    def get_background_items(self, folder):
        return []
