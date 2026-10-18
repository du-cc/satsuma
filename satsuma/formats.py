"""Format catalogue: which conversions, tools and actions apply to a file.

The conversion matrix mirrors the one Tangerine ships (275 pairs on macOS).
Apple-only formats are swapped for their Linux equivalents: Pages becomes
ODT, RTFD and text clippings are dropped, and ODT/ODP/HTML are accepted as
inputs.
"""

from __future__ import annotations

import os
import re

IMAGE_TARGETS = ["jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "pdf"]
IMAGE_SOURCES = ["jpg", "png", "webp", "heic", "tiff", "svg", "avif", "bmp"]
AUDIO = ["mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma"]
VIDEO = ["mp4", "mov", "mkv", "webm", "avi", "wmv"]
SUBTITLES = ["srt", "vtt"]
DOCUMENT_SOURCES = ["docx", "odt", "md", "rtf", "html"]
SPREADSHEETS = ["xlsx", "xls", "ods", "csv", "tsv"]
SLIDES = ["ppt", "pptx", "odp"]
ARCHIVES = ["zip", "tar", "gz", "rar"]
DOCUMENT_TARGETS = ["docx", "odt", "pdf", "txt", "md", "html", "epub", "png", "jpg"]
# Formats that can be read but are never offered as an output.
INPUT_ONLY = {"rar", "ppt", "xls", "rtf"}

ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "htm": "html",
    "markdown": "md", "aif": "aiff", "tgz": "gz", "oga": "ogg", "text": "txt",
    "m4v": "mp4",
}


def detect_format(path: str) -> str | None:
    """Return the canonical format key for a path, or None for folders/unknown."""
    if os.path.isdir(path):
        return None
    name = os.path.basename(path).lower()
    if name.endswith(".tar.gz"):
        return "gz"
    _, ext = os.path.splitext(name)
    ext = ext.lstrip(".")
    if not ext:
        return None
    ext = ALIASES.get(ext, ext)
    return ext if ext in ALL_KNOWN else None


def conversion_targets(fmt: str | None) -> list[str]:
    """Formats a file of ``fmt`` can be converted to, in wheel order."""
    if fmt is None:
        return []
    if fmt in IMAGE_SOURCES:
        targets = IMAGE_TARGETS
    elif fmt in AUDIO:
        targets = AUDIO
    elif fmt in VIDEO:
        targets = VIDEO + ["gif", "mp3"]
    elif fmt == "gif":
        targets = VIDEO
    elif fmt == "pdf":
        targets = ["docx", "odt", "pptx", "xlsx", "jpg", "png", "epub", "txt", "md"]
    elif fmt in SLIDES:
        targets = ["pdf", "jpg", "png"]
    elif fmt in SPREADSHEETS:
        targets = SPREADSHEETS + ["pdf"]
    elif fmt in DOCUMENT_SOURCES:
        targets = DOCUMENT_TARGETS
    elif fmt == "epub":
        targets = ["pdf", "docx", "odt", "txt", "md", "html"]
    elif fmt == "txt":
        targets = ["docx", "odt", "pdf", "md", "html", "epub", "jpg", "png"]
    elif fmt in SUBTITLES:
        targets = SUBTITLES + ["txt"]
    elif fmt in ARCHIVES:
        targets = ["zip", "tar", "gz"]
    else:
        targets = []
    return [t for t in targets if t != fmt and t not in INPUT_ONLY]


def selection_targets(formats: list[str | None]) -> list[str]:
    """Conversion targets shared by every file in a selection.

    A target is offered when every file can either be converted to it or is
    already in that format, and at least one file actually needs converting.
    """
    if not formats or any(f is None for f in formats):
        return []
    first = conversion_targets(formats[0]) + [formats[0]]
    shared = [t for t in first
              if all(t == f or t in conversion_targets(f) for f in formats)]
    return [t for t in shared if any(t != f for f in formats)]


def category(fmt: str | None) -> str | None:
    if fmt in AUDIO:
        return "audio"
    if fmt in VIDEO or fmt == "gif":
        return "video"
    if fmt in SPREADSHEETS:
        return "spreadsheet"
    if fmt in ARCHIVES:
        return "archive"
    if fmt in SUBTITLES:
        return "subtitle"
    if fmt in IMAGE_SOURCES:
        return "image"
    if fmt == "pdf":
        return "pdf"
    if fmt in SLIDES:
        return "slides"
    if fmt in DOCUMENT_SOURCES or fmt in ("txt", "epub"):
        return "document"
    return None


def label(fmt: str) -> str:
    return "GZIP" if fmt == "gz" else fmt.upper()


_EXT_RE = re.compile(r"(?:\.tar\.gz|\.tgz|\.[^.]+)$", re.IGNORECASE)


def stem(name: str) -> str:
    return _EXT_RE.sub("", name)


def unique_path(directory: str, base: str, ext: str) -> str:
    """``base.ext`` in ``directory``, or ``base 2.ext``, ``base 3.ext``…

    ``ext`` of "" means a folder.
    """
    suffix = f".{ext}" if ext else ""
    candidate = os.path.join(directory, f"{base}{suffix}")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} {n}{suffix}")
        n += 1
    return candidate


def conversion_output(src: str, target: str, directory: str | None = None) -> str:
    directory = directory or os.path.dirname(os.path.abspath(src))
    ext = "tar.gz" if target == "gz" else target
    return unique_path(directory, stem(os.path.basename(src)), ext)


# --- File tools --------------------------------------------------------------

TOOLS = {
    "compress": ("COMPRESS", "Compress", ["package-x-generic-symbolic", "view-restore-symbolic"]),
    "removeMetadata": ("METADATA", "Edit Metadata", ["document-properties-symbolic", "dialog-information-symbolic"]),
    "editImage": ("EDIT", "Edit Photo", ["preferences-color-symbolic", "image-x-generic-symbolic"]),
    "annotateImage": ("ANNOTATE", "Annotate Photo", ["document-edit-symbolic", "edit-symbolic"]),
    "frameImage": ("ADD BG", "Add Background", ["image-x-generic-symbolic"]),
    "cropImage": ("CROP", "Crop Image", ["image-crop-symbolic", "zoom-fit-best-symbolic"]),
    "normalizeAudio": ("NORMALIZE", "Normalize Volume", ["audio-volume-high-symbolic"]),
    "muteVideo": ("MUTE", "Remove Audio", ["audio-volume-muted-symbolic"]),
    "trimVideo": ("TRIM", "Trim Video", ["edit-cut-symbolic"]),
    "splitPDF": ("SPLIT", "Split PDF", ["view-dual-symbolic", "view-paged-symbolic"]),
    "organizePDF": ("ORGANIZE", "Organize PDF", ["view-grid-symbolic", "view-app-grid-symbolic"]),
    "extractArchive": ("EXTRACT", "Extract Archive", ["folder-open-symbolic", "folder-symbolic"]),
    "createPDF": ("CREATE PDF", "Create PDF", ["document-new-symbolic", "x-office-document-symbolic"]),
    "mergePDF": ("MERGE PDF", "Merge PDFs", ["edit-copy-symbolic"]),
    "cropVideo": ("CROP", "Crop Video", ["image-crop-symbolic", "zoom-fit-best-symbolic"]),
    "changeVideoSpeed": ("SPEED", "Change Video Speed", ["media-seek-forward-symbolic"]),
    "joinVideos": ("JOIN", "Join Videos", ["media-playlist-consecutive-symbolic", "view-list-symbolic"]),
    "videoSnapshots": ("SNAPSHOT", "Video Snapshots", ["camera-photo-symbolic"]),
    "splitVideo": ("SPLIT", "Split Video", ["video-x-generic-symbolic"]),
    "audioToVideo": ("VISUALIZE", "Audio Visualizer", ["video-x-generic-symbolic"]),
    "trimAudio": ("TRIM", "Trim Audio", ["edit-cut-symbolic"]),
    "audioChannels": ("CHANNELS", "Convert Audio Channels", ["audio-speakers-symbolic", "audio-x-generic-symbolic"]),
    "createCollage": ("COLLAGE", "Create Collage", ["view-grid-symbolic", "view-app-grid-symbolic"]),
    "redactImage": ("REDACT", "Redact Photo", ["view-conceal-symbolic", "security-high-symbolic"]),
    "redactVideo": ("REDACT", "Redact Video", ["view-conceal-symbolic", "security-high-symbolic"]),
    "redactAudio": ("BLEEP", "Bleep Audio", ["microphone-disabled-symbolic", "audio-volume-muted-symbolic"]),
}

# Tools that make sense for, and accept, several files at once.
MULTI_ONLY_TOOLS = {"createPDF", "mergePDF", "joinVideos", "createCollage"}
MULTI_CAPABLE_TOOLS = {
    "compress", "removeMetadata", "cropImage", "normalizeAudio", "muteVideo",
    "splitPDF", "extractArchive", "createPDF", "mergePDF", "joinVideos",
    "createCollage",
}
# Tools that open an editor/parameter dialog before running.
INTERACTIVE_TOOLS = {
    "compress", "removeMetadata", "editImage", "annotateImage", "frameImage",
    "cropImage", "normalizeAudio", "trimVideo", "mergePDF", "organizePDF",
    "cropVideo", "changeVideoSpeed", "joinVideos", "videoSnapshots",
    "splitVideo", "audioToVideo", "trimAudio", "audioChannels",
    "createCollage", "redactImage", "redactVideo", "redactAudio",
}


def _tools_for(fmt: str | None, count: int) -> list[str]:
    if fmt in ("jpg", "png", "webp", "heic", "tiff", "avif", "bmp"):
        if count > 1:
            return ["compress", "removeMetadata", "cropImage", "createPDF", "createCollage"]
        return ["compress", "removeMetadata", "editImage", "annotateImage",
                "frameImage", "cropImage", "redactImage"]
    if fmt == "svg":
        return ["createPDF", "createCollage"] if count > 1 else []
    if fmt in ("mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff"):
        return ["compress", "removeMetadata", "normalizeAudio", "audioToVideo",
                "trimAudio", "audioChannels", "redactAudio"]
    if fmt in ("mp4", "mov", "mkv", "webm"):
        if count > 1:
            return ["compress", "removeMetadata", "muteVideo", "trimVideo",
                    "cropVideo", "changeVideoSpeed", "joinVideos"]
        return ["compress", "removeMetadata", "muteVideo", "trimVideo",
                "cropVideo", "changeVideoSpeed", "videoSnapshots", "splitVideo",
                "redactVideo"]
    if fmt == "gif":
        return ["removeMetadata"]
    if fmt == "pdf":
        if count > 1:
            return ["compress", "removeMetadata", "splitPDF", "mergePDF"]
        return ["compress", "removeMetadata", "splitPDF", "organizePDF"]
    if fmt in ARCHIVES:
        return ["extractArchive"]
    return []


def tool_targets(formats: list[str | None]) -> list[str]:
    """Tools offered for a selection (all files must share a tool family)."""
    if not formats or any(f is None for f in formats):
        return []
    count = len(formats)
    tools = _tools_for(formats[0], count)
    if count > 1:
        tools = [t for t in tools if t in MULTI_CAPABLE_TOOLS]
        tools = [t for t in tools if all(t in _tools_for(f, count) for f in formats)]
    else:
        tools = [t for t in tools if t not in MULTI_ONLY_TOOLS]
    return tools


# --- File actions -------------------------------------------------------------

ACTIONS = {
    "share": ("SHARE", "Share", ["send-to-symbolic", "emblem-shared-symbolic"],
              "Send files by email, Bluetooth, or to your phone with GSConnect/KDE Connect."),
    "float": ("FLOAT", "Float", ["view-pin-symbolic", "pin-symbolic"],
              "Keep files in a floating shelf, then drag them into another app."),
    "copy": ("COPY", "Copy", ["edit-copy-symbolic"],
             "Copy files to the clipboard, ready to paste in Files."),
    "move": ("MOVE", "Move", ["folder-symbolic"],
             "Choose a destination folder and move the originals there."),
    "copyPath": ("COPY PATH", "Copy Path", ["edit-paste-symbolic", "text-x-generic-symbolic"],
                 "Copy full file paths as text, one path per line."),
    "openTerminal": ("TERMINAL", "Terminal", ["utilities-terminal-symbolic"],
                     "Open a folder, or a file's containing folder, in a terminal."),
}


def action_targets() -> list[str]:
    return list(ACTIONS)


def item_title(mode: str, key: str) -> str:
    if mode == "conversions":
        return label(key)
    if mode == "tools":
        return TOOLS[key][0]
    return ACTIONS[key][0]


def item_name(mode: str, key: str) -> str:
    if mode == "conversions":
        return f"Convert to {label(key)}"
    if mode == "tools":
        return TOOLS[key][1]
    return ACTIONS[key][1]


def item_phrase(mode: str, key: str) -> str:
    """Lower-case phrase for hints: "convert to M4A", "copy path"."""
    if mode == "conversions":
        return f"convert to {label(key)}"
    return item_name(mode, key).lower()


def item_icons(mode: str, key: str) -> list[str]:
    if mode == "tools":
        return TOOLS[key][2]
    if mode == "actions":
        return ACTIONS[key][2]
    return []


ALL_KNOWN = set(
    IMAGE_SOURCES + IMAGE_TARGETS + AUDIO + VIDEO + SUBTITLES + DOCUMENT_SOURCES
    + SPREADSHEETS + SLIDES + ARCHIVES + DOCUMENT_TARGETS
    + ["gif", "pdf", "epub", "txt", "pptx"]
)
