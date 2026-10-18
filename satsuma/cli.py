"""Headless command line: convert, run tools, list options, check converters."""

from __future__ import annotations

import argparse
import json
import sys

from . import VERSION
from . import backends as B
from . import formats as F

COMMANDS = ("convert", "tool", "list", "doctor", "install-integration", "uninstall-integration")


def _params(pairs: list[str]) -> dict:
    params = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="satsuma", description="Satsuma file converter")
    parser.add_argument("--version", action="version", version=f"Satsuma {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="convert files to another format")
    convert.add_argument("files", nargs="+")
    convert.add_argument("--to", required=True, help="target format, e.g. mp3, pdf, webp")
    convert.add_argument("--output-dir", help="folder for results (default: next to each file)")

    tool = sub.add_parser("tool", help="run a file tool (compress, trimVideo, mergePDF…)")
    tool.add_argument("name", choices=sorted(F.TOOLS))
    tool.add_argument("files", nargs="+")
    tool.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                      help='tool parameter, JSON values allowed, e.g. --set quality=60 --set rect=[0,0,100,100]')
    tool.add_argument("--output-dir")

    listing = sub.add_parser("list", help="show what a file can become")
    listing.add_argument("files", nargs="+")
    listing.add_argument("--json", action="store_true")

    sub.add_parser("doctor", help="check which converters are installed")
    sub.add_parser("install-integration", help="add the Satsuma menu and shortcut to GNOME Files")
    sub.add_parser("uninstall-integration", help="remove the GNOME Files menu and shortcut")
    args = parser.parse_args(argv)

    if args.command == "convert":
        failed = 0
        for path in args.files:
            fmt = F.detect_format(path)
            if args.to not in F.conversion_targets(fmt):
                print(f"✗ {path}: cannot convert {F.label(fmt or '?')} to {F.label(args.to)}", file=sys.stderr)
                failed += 1
                continue
            out = F.conversion_output(path, args.to, args.output_dir)
            try:
                print(B.convert(B.Runner(), path, args.to, out))
            except B.ConversionError as exc:
                print(f"✗ {path}: {exc}", file=sys.stderr)
                failed += 1
        return 1 if failed else 0

    if args.command == "tool":
        from . import tools as T
        formats = [F.detect_format(p) for p in args.files]
        if args.name not in F.tool_targets(formats):
            print(f"✗ {F.TOOLS[args.name][1]} does not apply to this selection", file=sys.stderr)
            return 1
        try:
            for path in T.run_tool(B.Runner(), args.name, args.files, _params(args.set), args.output_dir):
                print(path)
        except B.ConversionError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "list":
        formats = [F.detect_format(p) for p in args.files]
        data = {"conversions": F.selection_targets(formats), "tools": F.tool_targets(formats),
                "actions": F.action_targets()}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print("Convert to: " + (", ".join(F.label(t) for t in data["conversions"]) or "—"))
            print("Tools:      " + (", ".join(F.TOOLS[t][1] for t in data["tools"]) or "—"))
            print("Actions:    " + ", ".join(F.ACTIONS[a][1] for a in data["actions"]))
        return 0

    if args.command == "doctor":
        missing = []
        for row in B.readiness():
            mark = "✓" if row["path"] else "✗"
            print(f"{mark} {row['name']:<16} {row['purpose']}")
            if not row["path"]:
                missing.append(row["package"])
        if missing:
            print("\nMissing: " + "; ".join(missing))
        return 0

    from . import integration
    if args.command == "install-integration":
        integration.install()
        print("Installed. Restart Files with: nautilus -q")
    else:
        integration.uninstall()
        print("Removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
