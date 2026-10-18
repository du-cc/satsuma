"""Small JSON-backed preferences store (~/.config/satsuma/settings.json)."""

from __future__ import annotations

import json
import os

from gi.repository import GLib

DEFAULTS = {
    "output_dir": "",            # "" = next to the original
    "sound": True,               # tick as the highlight moves
    "notifications": True,       # desktop notification when a job finishes unfocused
    "default_wheel": "conversions",
    "close_after_action": True,  # for wheels opened from Files or the command line
    "wheel_scale": 1.15,
    "parallel_jobs": max(1, min(4, (os.cpu_count() or 2) // 2)),
}


class Settings:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(GLib.get_user_config_dir(), "satsuma", "settings.json")
        self.values = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self.values.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value
        self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.values, fh, indent=2)
        os.replace(tmp, self.path)

    def output_dir(self) -> str | None:
        """The chosen output folder, or None to save beside each original."""
        directory = self.values.get("output_dir") or ""
        return directory if directory and os.path.isdir(directory) else None
