#!/bin/bash
# Install Satsuma for the current user on Fedora (GNOME).
#   ./install.sh            install, asking before running dnf
#   ./install.sh --yes      install without asking
#   ./install.sh --no-deps  skip dnf entirely
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

YES=0 DEPS=1
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --no-deps) DEPS=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

have() { for c in "$@"; do command -v "$c" >/dev/null 2>&1 && return 0; done; return 1; }
pyhas() { /usr/bin/python3 -c "$1" >/dev/null 2>&1; }
rpmhas() { rpm -q --whatprovides "$1" >/dev/null 2>&1; }

# Check for each tool, not a package name, so builds from other repos
# (RPM Fusion ffmpeg, 7zip instead of p7zip, ...) count as installed.
missing=()
need() { missing+=("$@"); }
pyhas 'import gi'                                              || need python3-gobject
pyhas 'import cairo'                                           || need python3-cairo
pyhas 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' || need gtk4
pyhas 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw'   || need libadwaita
rpmhas gstreamer1-plugins-base                                 || need gstreamer1-plugins-base
have ffmpeg                  || need ffmpeg-free
have magick convert          || need ImageMagick
have soffice libreoffice     || need libreoffice-writer
rpmhas libreoffice-calc      || need libreoffice-calc
rpmhas libreoffice-impress   || need libreoffice-impress
have pandoc                  || need pandoc
have pdftoppm                || need poppler-utils
have qpdf                    || need qpdf
have gs                      || need ghostscript
have exiftool                || need perl-Image-ExifTool
have rsvg-convert            || need librsvg2-tools
have heif-enc                || need libheif-tools
have avifenc                 || need libavif-tools
have img2pdf                 || need python3-img2pdf
have 7z 7zz 7za unrar        || need 7zip
have tesseract               || need tesseract tesseract-langpack-eng
rpmhas nautilus-python       || need nautilus-python

if [ "$DEPS" = 1 ] && command -v dnf >/dev/null && [ ${#missing[@]} -gt 0 ]; then
  echo "Satsuma would like these Fedora packages:"
  printf '  %s\n' "${missing[@]}"
  answer=y
  if [ "$YES" = 0 ]; then
    read -r -p "Install them now with sudo dnf? [Y/n] " answer
  fi
  if [[ "${answer:-y}" =~ ^[Yy]?$ ]]; then
    # One at a time, so one unavailable or conflicting package can't block the rest.
    failed=()
    for pkg in "${missing[@]}"; do
      sudo dnf install -y "$pkg" >/dev/null 2>&1 && echo "  ✓ $pkg" || { echo "  ✗ $pkg"; failed+=("$pkg"); }
    done
    if [ ${#failed[@]} -gt 0 ]; then
      echo "Could not install: ${failed[*]} — Satsuma will dim what needs them (see: satsuma doctor)."
    fi
  else
    echo "Skipping. Satsuma dims options whose converter is missing."
  fi
elif [ "$DEPS" = 1 ] && ! command -v dnf >/dev/null && [ ${#missing[@]} -gt 0 ]; then
  echo "dnf not found; install GTK 4, libadwaita, PyGObject and the converters with your package manager."
fi

DATA=${XDG_DATA_HOME:-$HOME/.local/share}
LIB=$DATA/satsuma
BIN=$HOME/.local/bin

echo "Installing to $LIB"
rm -rf "$LIB.new"
mkdir -p "$LIB.new"
cp -r satsuma "$LIB.new/"
find "$LIB.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$LIB"
mv "$LIB.new" "$LIB"

mkdir -p "$BIN"
cat > "$BIN/satsuma" <<LAUNCHER
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec ${SATSUMA_PYTHON:-/usr/bin/python3} -m satsuma "\$@"
LAUNCHER
chmod +x "$BIN/satsuma"

mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps" "$DATA/icons/hicolor/symbolic/apps"
sed "s|^Exec=satsuma|Exec=$BIN/satsuma|" satsuma/data/io.github.satsuma.Satsuma.desktop \
  > "$DATA/applications/io.github.satsuma.Satsuma.desktop"
cp satsuma/data/icons/hicolor/scalable/apps/*.svg "$DATA/icons/hicolor/scalable/apps/"
cp satsuma/data/icons/hicolor/symbolic/apps/*.svg "$DATA/icons/hicolor/symbolic/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" || true

# Stop a Satsuma that is still running from before, so the next F4 uses this version.
gdbus call --session --dest io.github.satsuma.Satsuma --object-path /io/github/satsuma/Satsuma \
  --method org.freedesktop.Application.ActivateAction quit "[]" "{}" >/dev/null 2>&1 || true
pkill -f "python3 -m satsuma" >/dev/null 2>&1 || true

# Files hotkey (F4), context menu and D-Bus activation for an instant wheel.
"$BIN/satsuma" install-integration
nautilus -q >/dev/null 2>&1 || true

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: add $BIN to your PATH to run 'satsuma' from a terminal." ;; esac
cat <<DONE

Satsuma is installed.
  • Log out and back in once so GNOME Shell loads the Satsuma Wheel extension,
    which opens the wheel at your mouse pointer (until then it opens centred).
  • In Files, select a file and press F4 — the wheel pops up at your pointer.
    Shift / Alt / Ctrl switch between Convert, Tools and Actions; arrows + Enter or click to choose.
  • Right-click → Satsuma for the same options (needs nautilus-python).
  • Or open Satsuma from Activities and drag files onto it.
  • Check converters: satsuma doctor
DONE
