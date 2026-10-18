"""Install the GNOME Files hotkey, context menu and D-Bus activation."""

from __future__ import annotations

import ast
import glob
import os
import shlex
import shutil
import subprocess
import sys
import time

from . import APP_ID

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_NAME = "Satsuma Wheel"
DEFAULT_ACCEL = "F4"
OBJECT_PATH = "/" + APP_ID.replace(".", "/")


def _home() -> str:
    return os.path.expanduser("~")


def _data() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(_home(), ".local", "share")


def _config() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")


def extension_path() -> str:
    return os.path.join(_data(), "nautilus-python", "extensions", "satsuma_nautilus.py")


def script_path() -> str:
    return os.path.join(_data(), "nautilus", "scripts", SCRIPT_NAME)


def accels_path() -> str:
    return os.path.join(_config(), "nautilus", "scripts-accels")


def service_path() -> str:
    return os.path.join(_data(), "dbus-1", "services", f"{APP_ID}.service")


SHELL_UUID = "satsuma-wheel@satsuma.github.io"
SYSTEM_EXTENSION = "/usr/share/nautilus-python/extensions/satsuma_nautilus.py"
SYSTEM_SERVICE = f"/usr/share/dbus-1/services/{APP_ID}.service"


def shell_extension_dir() -> str:
    return os.path.join(_data(), "gnome-shell", "extensions", SHELL_UUID)


def _gsettings(*args) -> str | None:
    exe = shutil.which("gsettings")
    if not exe:
        return None
    result = subprocess.run([exe, *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def shell_extension_enabled() -> bool:
    value = _gsettings("get", "org.gnome.shell", "enabled-extensions") or ""
    return SHELL_UUID in value


def enable_shell_extension():
    """Turn the extension on; GNOME Shell loads it at the next login."""
    if shutil.which("gnome-extensions"):
        if subprocess.run(["gnome-extensions", "enable", SHELL_UUID],
                          capture_output=True).returncode == 0:
            return
    value = _gsettings("get", "org.gnome.shell", "enabled-extensions")
    if value is None:
        return
    current = [] if value.startswith("@as") else ast.literal_eval(value)
    if SHELL_UUID not in current:
        current.append(SHELL_UUID)
        _gsettings("set", "org.gnome.shell", "enabled-extensions", str(current))


def satsuma_command() -> str:
    found = shutil.which("satsuma")
    if found:
        return found
    launcher = os.path.join(_home(), ".local", "bin", "satsuma")
    return launcher if os.path.exists(launcher) else f"{sys.executable} -m satsuma"


def nautilus_python_present() -> bool:
    patterns = ["/usr/lib*/nautilus/extensions-4/libnautilus-python.so",
                "/usr/lib/*/nautilus/extensions-4/libnautilus-python.so"]
    return any(glob.glob(p) for p in patterns)


def current_accel() -> str | None:
    try:
        with open(accels_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith(";") and line.endswith(" " + SCRIPT_NAME):
                    return line[: -len(SCRIPT_NAME)].strip()
    except OSError:
        pass
    return None


def status() -> dict:
    return {"extension": os.path.exists(extension_path()) or os.path.exists(SYSTEM_EXTENSION),
            "script": os.path.exists(script_path()),
            "service": os.path.exists(service_path()) or os.path.exists(SYSTEM_SERVICE),
            "accel": current_accel(),
            "shell_extension": os.path.exists(shell_extension_dir()) and shell_extension_enabled(),
            "nautilus_python": nautilus_python_present()}


def _backup(path: str):
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{time.strftime('%Y%m%d%H%M%S')}")


SCRIPT = r"""#!/bin/bash
# Opens the Satsuma wheel for the files selected in GNOME Files.
# Talks to the running (or D-Bus-started) Satsuma so the wheel appears at once.
mapfile -t paths <<< "${NAUTILUS_SCRIPT_SELECTED_FILE_PATHS%$'\n'}"
[ -n "${paths[0]}" ] || exit 0
quote() { local s=${1//\\/\\\\}; s=${s//\'/\\\'}; printf "'%s'" "$s"; }
list=""
for p in "${paths[@]}"; do list+="${list:+, }$(quote "$p")"; done
data="{}"
token=${XDG_ACTIVATION_TOKEN:-$DESKTOP_STARTUP_ID}
[ -n "$token" ] && data="{'activation-token': <$(quote "$token")>, 'desktop-startup-id': <$(quote "$token")>}"
if timeout 20 gdbus call --session --timeout 15 --dest @APP_ID@ --object-path @OBJECT_PATH@ \
    --method org.freedesktop.Application.ActivateAction wheel "[<@as [$list]>]" "$data" \
    >/dev/null 2>&1; then
  exit 0
fi
exec @COMMAND@ --wheel "${paths[@]}"
"""


def render_extension(command: str, lib: str) -> str:
    with open(os.path.join(HERE, "data", "nautilus", "satsuma_nautilus.py"), encoding="utf-8") as fh:
        source = fh.read()
    return (source.replace("@SATSUMA_COMMAND@", command).replace("@SATSUMA_LIB@", lib)
            .replace("@APP_ID@", APP_ID).replace("@OBJECT_PATH@", OBJECT_PATH))


def render_service(command: str) -> str:
    return f"[D-BUS Service]\nName={APP_ID}\nExec={command} --gapplication-service\n"


def install(accel: str | None = None):
    command = satsuma_command()
    lib = os.path.dirname(HERE)
    quoted = " ".join(shlex.quote(p) for p in shlex.split(command))

    # A system-wide (RPM) install already ships the menu and D-Bus service.
    if not os.path.exists(SYSTEM_EXTENSION):
        ext = extension_path()
        os.makedirs(os.path.dirname(ext), exist_ok=True)
        with open(ext, "w", encoding="utf-8") as fh:
            fh.write(render_extension(command, lib))

    script = script_path()
    os.makedirs(os.path.dirname(script), exist_ok=True)
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(SCRIPT.replace("@APP_ID@", APP_ID).replace("@OBJECT_PATH@", OBJECT_PATH)
                 .replace("@COMMAND@", quoted))
    os.chmod(script, 0o755)

    if not os.path.exists(SYSTEM_SERVICE):
        service = service_path()
        os.makedirs(os.path.dirname(service), exist_ok=True)
        with open(service, "w", encoding="utf-8") as fh:
            fh.write(render_service(quoted))

    set_accel(accel or current_accel() or DEFAULT_ACCEL)

    # GNOME Shell extension that opens the wheel at the pointer.
    source = os.path.join(HERE, "data", "gnome-shell", SHELL_UUID)
    target = shell_extension_dir()
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(source, target)
    enable_shell_extension()


def set_accel(accel: str):
    """Bind ``accel`` (GTK syntax, e.g. F4 or <Control><Alt>c) to the wheel script in Files."""
    path = accels_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    kept = [line for line in lines
            if line.lstrip().startswith(";") or not line.strip().endswith(" " + SCRIPT_NAME)]
    new = kept + [f"{accel} {SCRIPT_NAME}"]
    if new != lines:
        _backup(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new) + "\n")


def uninstall():
    for path in (extension_path(), script_path(), service_path()):
        if os.path.exists(path):
            os.remove(path)
    if os.path.exists(shell_extension_dir()):
        if shutil.which("gnome-extensions"):
            subprocess.run(["gnome-extensions", "disable", SHELL_UUID], capture_output=True)
        shutil.rmtree(shell_extension_dir())
    path = accels_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        kept = [line for line in lines if not line.strip().endswith(" " + SCRIPT_NAME)]
        if kept != lines:
            _backup(path)
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
