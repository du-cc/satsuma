"""Conversion back ends built on standard Fedora command-line tools.

Every conversion writes into a private temporary path next to the output
and is renamed into place only when it succeeds, so a failed or cancelled
job never leaves a half-written file behind.
"""

from __future__ import annotations

import csv
import functools
import glob
import html
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile

from . import formats as F


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


# --- Tool discovery -------------------------------------------------------------

def which(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


@functools.lru_cache(maxsize=None)
def tool(name: str) -> str | None:
    candidates = {
        "magick": ("magick", "convert"),
        "soffice": ("soffice", "libreoffice"),
        "7z": ("7z", "7za", "7zz"),
        "unrar": ("unrar", "unar"),
    }.get(name, (name,))
    return which(*candidates)


@functools.lru_cache(maxsize=None)
def ffmpeg_encoders() -> frozenset[str]:
    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        return frozenset()
    out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                         text=True).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return frozenset(names)


@functools.lru_cache(maxsize=None)
def magick_formats() -> dict[str, str]:
    """Map ImageMagick format name → mode string such as 'rw+'."""
    magick = tool("magick")
    if not magick:
        return {}
    cmd = [magick, "-list", "format"]
    if os.path.basename(magick) == "convert":
        cmd = [magick, "-list", "format"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    result = {}
    for line in out.splitlines():
        m = re.match(r"\s*([A-Z0-9-]+)\*?\s+\S+\s+([r-][w-][+-])", line)
        if m:
            result[m.group(1).lower()] = m.group(2)
    return result


def magick_can(fmt: str, mode: str) -> bool:
    flags = magick_formats().get(fmt, "")
    return ("r" in flags) if mode == "r" else ("w" in flags)


def pick_encoder(*names: str) -> str | None:
    encoders = ffmpeg_encoders()
    for name in names:
        if name in encoders:
            return name
    return None


# --- Process runner ---------------------------------------------------------------

class Runner:
    """Runs external commands for one job, honouring cancellation."""

    def __init__(self):
        self.cancel_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled("Cancelled")

    def run(self, cmd: list[str], *, cwd: str | None = None, env: dict | None = None,
            stdout_path: str | None = None) -> str:
        self.check()
        stdout_file = open(stdout_path, "wb") if stdout_path else None
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout_file or subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = self._proc.communicate()
        except FileNotFoundError as exc:
            raise ConversionError(f"{cmd[0]} is not installed") from exc
        finally:
            if stdout_file:
                stdout_file.close()
        self.check()
        if self._proc.returncode != 0:
            tail = (err or b"").decode(errors="replace").strip().splitlines()[-6:]
            raise ConversionError(f"{os.path.basename(cmd[0])} failed: " + "\n".join(tail))
        return (out or b"").decode(errors="replace")


def require(name: str, package: str) -> str:
    path = tool(name)
    if not path:
        raise ConversionError(f"Install {package} to use this conversion "
                              f"(sudo dnf install {package})")
    return path


# --- Atomic output helpers -----------------------------------------------------------

class Staging:
    """A scratch directory beside the output that is cleaned up on exit."""

    def __init__(self, final_path: str):
        self.final = final_path
        parent = os.path.dirname(final_path) or "."
        self.dir = tempfile.mkdtemp(prefix=".satsuma-", dir=parent)

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def commit(self, produced: str) -> str:
        if not os.path.exists(produced):
            raise ConversionError("The converter did not produce an output file")
        final = self.final
        if os.path.exists(final):  # someone raced us; pick a fresh name
            base, ext = os.path.splitext(final)
            if final.endswith(".tar.gz"):
                base, ext = final[:-7], ".tar.gz"
            final = F.unique_path(os.path.dirname(final), os.path.basename(base),
                                  ext.lstrip("."))
        os.replace(produced, final)
        return final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


def collect_pages(produced: list[str], final: str, ext: str, staging: Staging) -> str:
    """One page → a file; several pages → a folder named after ``final``."""
    produced = sorted(produced)
    if not produced:
        raise ConversionError("No pages were produced")
    if len(produced) == 1:
        return staging.commit(produced[0])
    folder_final = final[: -(len(ext) + 1)]
    folder_final = F.unique_path(os.path.dirname(folder_final),
                                 os.path.basename(folder_final), "")
    folder = staging.path("pages")
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(folder_final)
    for i, page in enumerate(produced, 1):
        os.replace(page, os.path.join(folder, f"{name} {i:0{len(str(len(produced)))}d}.{ext}"))
    os.replace(folder, folder_final)
    return folder_final


# --- Conversion entry point -------------------------------------------------------------

def convert(runner: Runner, src: str, target: str, out: str) -> str:
    """Convert ``src`` to ``target`` writing ``out``; returns the final path."""
    fmt = F.detect_format(src)
    if target not in F.conversion_targets(fmt):
        raise ConversionError(f"{F.label(fmt or '?')} → {F.label(target)} is not supported")
    cat = F.category(fmt)
    with Staging(out) as st:
        if cat == "image":
            return _image(runner, src, fmt, target, out, st)
        if cat == "audio" or (cat == "video" and target == "mp3"):
            return _audio(runner, src, target, st)
        if cat == "video":
            return _video(runner, src, fmt, target, st)
        if fmt == "pdf":
            return _pdf(runner, src, target, out, st)
        if cat == "slides":
            return _slides(runner, src, target, out, st)
        if cat == "spreadsheet":
            return _sheet(runner, src, fmt, target, st)
        if cat == "document":
            return _document(runner, src, fmt, target, out, st)
        if cat == "subtitle":
            return _subtitle(runner, src, target, st)
        if cat == "archive":
            return _archive(runner, src, fmt, target, st)
    raise ConversionError("Unsupported conversion")


# --- Images ------------------------------------------------------------------------------

def _image_to_png(runner: Runner, src: str, fmt: str, st: Staging) -> str:
    """Rasterise inputs ImageMagick may not read directly into a PNG."""
    png = st.path("raster.png")
    if fmt == "svg" and tool("rsvg-convert"):
        runner.run([tool("rsvg-convert"), "-f", "png", "-o", png, src])
        return png
    if fmt == "heic" and not magick_can("heic", "r") and tool("heif-dec"):
        runner.run([tool("heif-dec"), src, png])
        return png
    if fmt == "heic" and not magick_can("heic", "r") and tool("heif-convert"):
        runner.run([tool("heif-convert"), src, png])
        return png
    return src


def _image(runner, src, fmt, target, out, st):
    magick = require("magick", "ImageMagick")
    source = _image_to_png(runner, src, fmt, st)
    frame = source + "[0]" if fmt in ("tiff", "heic", "avif", "webp") else source
    result = st.path(f"out.{target}")
    if target == "pdf":
        if tool("img2pdf") and fmt in ("jpg", "png", "tiff"):
            runner.run([tool("img2pdf"), source, "-o", result])
        else:
            runner.run([magick, frame, "-auto-orient", result])
        return st.commit(result)
    if target == "heic" and not magick_can("heic", "w"):
        enc = require("heif-enc", "libheif-tools")
        png = st.path("for-heic.png")
        runner.run([magick, frame, "-auto-orient", png])
        try:
            runner.run([enc, "-q", "85", png, "-o", result])
        except Cancelled:
            raise
        except ConversionError as exc:
            if "HEVC" in str(exc):
                raise ConversionError("HEIC output needs an HEVC encoder: enable RPM Fusion and "
                                      "run sudo dnf install libheif-freeworld") from exc
            raise
        return st.commit(result)
    if target == "avif" and not magick_can("avif", "w"):
        enc = tool("avifenc") or tool("heif-enc")
        if not enc:
            raise ConversionError("Install libavif-tools to write AVIF (sudo dnf install libavif-tools)")
        png = st.path("for-avif.png")
        runner.run([magick, frame, "-auto-orient", png])
        args = [enc, png, result] if os.path.basename(enc) == "avifenc" else [enc, "-A", png, "-o", result]
        try:
            runner.run(args)
        except Cancelled:
            raise
        except ConversionError as exc:
            if "AV1" in str(exc):
                raise ConversionError("AVIF output needs an AV1 encoder: "
                                      "sudo dnf install libavif-tools") from exc
            raise
        return st.commit(result)
    args = [magick, frame, "-auto-orient"]
    if target in ("jpg", "bmp"):
        args += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
    if target == "jpg":
        args += ["-quality", "92"]
    elif target in ("webp", "heic", "avif"):
        args += ["-quality", "85"]
    runner.run(args + [result])
    return st.commit(result)


# --- Audio / video ------------------------------------------------------------------------

def _audio_codec(target: str) -> list[str]:
    if target == "mp3":
        return ["-c:a", pick_encoder("libmp3lame") or "mp3", "-q:a", "2"]
    if target == "m4a":
        return ["-c:a", pick_encoder("libfdk_aac", "aac") or "aac", "-b:a", "192k"]
    if target == "wav":
        return ["-c:a", "pcm_s16le"]
    if target == "flac":
        return ["-c:a", "flac"]
    if target == "ogg":
        return ["-c:a", pick_encoder("libvorbis", "vorbis") or "libvorbis", "-q:a", "5"] + (
            ["-strict", "-2"] if pick_encoder("libvorbis") is None else [])
    if target == "opus":
        return ["-c:a", pick_encoder("libopus", "opus") or "libopus", "-b:a", "128k"] + (
            ["-strict", "-2"] if pick_encoder("libopus") is None else [])
    if target == "aiff":
        return ["-c:a", "pcm_s16be"]
    if target == "wma":
        return ["-c:a", "wmav2", "-b:a", "192k"]
    raise ConversionError(f"No audio encoder for {target}")


def ffmpeg_base() -> list[str]:
    return [require("ffmpeg", "ffmpeg-free"), "-hide_banner", "-loglevel", "error",
            "-nostdin", "-y"]


def _audio(runner, src, target, st):
    result = st.path(f"out.{target}")
    runner.run(ffmpeg_base() + ["-i", src, "-map", "0:a:0", "-map_metadata", "0", "-vn"]
               + _audio_codec(target) + [result])
    return st.commit(result)


def h264_args(crf: int = 20) -> list[str]:
    enc = pick_encoder("libx264", "libopenh264", "h264_vaapi")
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if enc == "libopenh264":
        bitrate = {18: "10M", 20: "8M", 23: "5M", 28: "2500k", 32: "1200k"}.get(crf, "5M")
        return ["-c:v", "libopenh264", "-b:v", bitrate, "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"]


def video_codec(target: str, crf: int = 20) -> list[str]:
    aac = ["-c:a", pick_encoder("libfdk_aac", "aac") or "aac", "-b:a", "160k"]
    if target in ("mp4", "mov"):
        return h264_args(crf) + aac + ["-movflags", "+faststart"]
    if target == "mkv":
        return h264_args(crf) + aac
    if target == "webm":
        vp9 = pick_encoder("libvpx-vp9", "libvpx")
        if vp9 is None:
            raise ConversionError("This ffmpeg build cannot encode WebM")
        return ["-c:v", vp9, "-crf", str(crf + 12), "-b:v", "0", "-row-mt", "1",
                "-pix_fmt", "yuv420p"] + _audio_codec("opus")
    if target == "avi":
        return ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"] + _audio_codec("mp3")
    if target == "wmv":
        return ["-c:v", "wmv2", "-q:v", "3"] + _audio_codec("wma")
    raise ConversionError(f"No video encoder for {target}")


EVEN_SCALE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _video(runner, src, fmt, target, st):
    result = st.path(f"out.{target}")
    base = ffmpeg_base()
    if target == "gif":
        vf = ("fps=12,scale='min(720,iw)':-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a")
        runner.run(base + ["-i", src, "-filter_complex", vf, "-loop", "0", result])
        return st.commit(result)
    # Container-only changes between MP4, MOV and MKV keep the original streams.
    if fmt in ("mp4", "mov", "mkv") and target in ("mp4", "mov", "mkv"):
        try:
            extra = ["-movflags", "+faststart"] if target != "mkv" else []
            runner.run(base + ["-i", src, "-map", "0:v?", "-map", "0:a?", "-c", "copy"]
                       + extra + [result])
            return st.commit(result)
        except Cancelled:
            raise
        except ConversionError:
            if os.path.exists(result):
                os.remove(result)
    vf = ["-vf", EVEN_SCALE] if fmt != "gif" else ["-vf", EVEN_SCALE, "-an"]
    runner.run(base + ["-i", src, "-map", "0:v:0", "-map", "0:a:0?"] + vf
               + video_codec(target) + [result])
    return st.commit(result)


# --- Office documents -----------------------------------------------------------------------

def soffice(runner: Runner, src: str, convert_to: str, outdir: str,
            infilter: str | None = None) -> str:
    office = require("soffice", "libreoffice-writer libreoffice-calc libreoffice-impress")
    profile = tempfile.mkdtemp(prefix="satsuma-lo-")
    try:
        args = [office, f"-env:UserInstallation=file://{profile}", "--headless",
                "--norestore", "--nologo"]
        if infilter:
            args.append(f"--infilter={infilter}")
        runner.run(args + ["--convert-to", convert_to, "--outdir", outdir, src])
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    ext = convert_to.split(":", 1)[0]
    produced = os.path.join(outdir, F.stem(os.path.basename(src)) + "." + ext)
    if not os.path.exists(produced):
        found = glob.glob(os.path.join(outdir, f"*.{ext}"))
        if not found:
            raise ConversionError("LibreOffice did not produce an output file")
        produced = found[0]
    return produced


def pandoc(runner: Runner, src: str, dst: str, src_format: str | None = None,
           dst_format: str | None = None, extra: list[str] | None = None):
    exe = require("pandoc", "pandoc")
    args = [exe, src, "-o", dst, "--standalone"]
    if src_format:
        args += ["-f", src_format]
    if dst_format:
        args += ["-t", dst_format]
    runner.run(args + (extra or []), cwd=os.path.dirname(os.path.abspath(src)))


def rasterise_pdf(runner: Runner, pdf: str, target: str, prefix: str,
                  dpi: int = 150) -> list[str]:
    exe = require("pdftoppm", "poppler-utils")
    flag = "-jpeg" if target == "jpg" else "-png"
    runner.run([exe, flag, "-r", str(dpi), pdf, prefix])
    return sorted(glob.glob(prefix + "*." + ("jpg" if target == "jpg" else "png")))


def txt_to_html(src: str, dst: str):
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    body = "\n".join("<p>" + "<br/>\n".join(html.escape(l) for l in p.splitlines()) + "</p>"
                     for p in paragraphs)
    title = html.escape(F.stem(os.path.basename(src)))
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
                 f"</head><body>{body}</body></html>")


PANDOC_READERS = {"md": "markdown", "html": "html", "epub": "epub", "docx": "docx",
                  "odt": "odt", "rtf": "rtf"}
PANDOC_WRITERS = {"md": "gfm", "html": "html5", "epub": "epub3", "docx": "docx",
                  "odt": "odt", "txt": "plain"}
OFFICE_FILTERS = {
    "docx": "docx:MS Word 2007 XML", "odt": "odt", "pdf": "pdf",
    "txt": "txt:Text (encoded):UTF8", "html": "html:XHTML Writer File:UTF8",
    "rtf": "rtf",
}


def _document(runner, src, fmt, target, out, st):
    office_source = fmt in ("docx", "odt", "rtf")
    source, source_fmt = src, fmt
    if fmt == "txt":
        source, source_fmt = st.path("text.html"), "html"
        txt_to_html(src, source)

    if target in ("pdf", "png", "jpg"):
        if office_source:
            pdf = soffice(runner, src, "pdf", st.dir)
        else:
            docx = st.path("intermediate.docx")
            pandoc(runner, source, docx, PANDOC_READERS[source_fmt])
            pdf = soffice(runner, docx, "pdf", st.dir)
        if target == "pdf":
            return st.commit(pdf)
        pages = rasterise_pdf(runner, pdf, target, st.path("page"))
        return collect_pages(pages, out, target, st)

    result = st.path(f"out.{target}")
    if office_source and target in ("docx", "odt", "txt", "html"):
        produced = soffice(runner, src, OFFICE_FILTERS[target], st.path("lo"))
        return st.commit(produced)
    if fmt == "rtf":  # pandoc's RTF reader is young; go through LibreOffice.
        source = soffice(runner, src, OFFICE_FILTERS["docx"], st.path("lo"))
        source_fmt = "docx"
    extra = ["--metadata", f"title={F.stem(os.path.basename(src))}"] if target in ("epub", "html") else []
    pandoc(runner, source, result, PANDOC_READERS[source_fmt], PANDOC_WRITERS[target], extra)
    return st.commit(result)


def _pdf_text(runner: Runner, src: str, st: Staging) -> str:
    exe = require("pdftotext", "poppler-utils")
    txt = st.path("text.txt")
    runner.run([exe, "-enc", "UTF-8", src, txt])
    with open(txt, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if text.strip() or not tool("tesseract"):
        return txt
    # Scanned PDF: OCR each page locally.
    pages = rasterise_pdf(runner, src, "png", st.path("ocr"), dpi=300)
    chunks = []
    for page in pages:
        chunks.append(runner.run([tool("tesseract"), page, "-", "--psm", "3"]))
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("\n\f\n".join(chunks))
    return txt


def _pdf_html(runner: Runner, src: str, st: Staging) -> str:
    """Reflowable HTML of a PDF's text, for DOCX/ODT/EPUB/Markdown exports."""
    exe = tool("pdftohtml")
    if exe:
        html_path = st.path("pdf.html")
        runner.run([exe, "-s", "-i", "-noframes", "-enc", "UTF-8", "-q", src,
                    html_path[:-5]])
        produced = html_path if os.path.exists(html_path) else html_path[:-5] + "s.html"
        if os.path.exists(produced):
            with open(produced, encoding="utf-8", errors="replace") as fh:
                if re.sub(r"<[^>]+>|&nbsp;|\s", "", fh.read()):
                    return produced
    txt = _pdf_text(runner, src, st)
    html_path = st.path("pdftext.html")
    txt_to_html(txt, html_path)
    return html_path


def _pdf(runner, src, target, out, st):
    result = st.path(f"out.{target}")
    if target in ("jpg", "png"):
        pages = rasterise_pdf(runner, src, target, st.path("page"), dpi=300)
        return collect_pages(pages, out, target, st)
    if target == "txt":
        return st.commit(_pdf_text(runner, src, st))
    if target == "pptx":
        return st.commit(soffice(runner, src, "pptx", st.dir, infilter="impress_pdf_import"))
    if target == "xlsx":
        csv_path = st.path(F.stem(os.path.basename(src)) + ".csv")
        _pdf_tables_to_csv(runner, src, csv_path, st)
        return st.commit(soffice(runner, csv_path, "xlsx", st.path("lo"),
                                 infilter="CSV:44,34,76,1"))
    if target in ("docx", "odt"):
        try:
            import pdf2docx  # type: ignore  # optional, better layout
        except ImportError:
            pdf2docx = None
        if pdf2docx and target == "docx":
            converter = pdf2docx.Converter(src)
            try:
                converter.convert(result)
            finally:
                converter.close()
            return st.commit(result)
    html_path = _pdf_html(runner, src, st)
    extra = ["--metadata", f"title={F.stem(os.path.basename(src))}"] if target == "epub" else []
    pandoc(runner, html_path, result, "html", PANDOC_WRITERS[target], extra)
    return st.commit(result)


def _pdf_tables_to_csv(runner, src, csv_path, st):
    exe = require("pdftotext", "poppler-utils")
    layout = st.path("layout.txt")
    runner.run([exe, "-layout", "-enc", "UTF-8", src, layout])
    with open(layout, encoding="utf-8", errors="replace") as fh, \
            open(csv_path, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        for line in fh:
            line = line.rstrip("\n\f")
            if line.strip():
                writer.writerow([cell.strip() for cell in re.split(r"\s{2,}", line.strip())])


def _slides(runner, src, target, out, st):
    pdf = soffice(runner, src, "pdf", st.dir)
    if target == "pdf":
        return st.commit(pdf)
    pages = rasterise_pdf(runner, pdf, target, st.path("slide"), dpi=150)
    return collect_pages(pages, out, target, st)


# --- Spreadsheets ---------------------------------------------------------------------------

SHEET_FILTERS = {
    "xlsx": "xlsx:Calc MS Excel 2007 XML", "ods": "ods", "pdf": "pdf",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1",
    "tsv": "csv:Text - txt - csv (StarCalc):9,34,76,1",
}


def _sheet(runner, src, fmt, target, st):
    if {fmt, target} <= {"csv", "tsv"}:
        result = st.path(f"out.{target}")
        with open(src, newline="", encoding="utf-8", errors="replace") as fh, \
                open(result, "w", newline="", encoding="utf-8") as out:
            reader = csv.reader(fh, delimiter="\t" if fmt == "tsv" else ",")
            writer = csv.writer(out, delimiter="\t" if target == "tsv" else ",")
            writer.writerows(reader)
        return st.commit(result)
    infilter = {"csv": "CSV:44,34,76,1", "tsv": "CSV:9,34,76,1"}.get(fmt)
    source = src
    if fmt == "tsv":  # LibreOffice picks its import filter from the extension.
        source = st.path(F.stem(os.path.basename(src)) + ".csv")
        shutil.copyfile(src, source)
    produced = soffice(runner, source, SHEET_FILTERS[target], st.path("lo"), infilter=infilter)
    if target == "tsv":
        renamed = produced[:-4] + ".tsv"
        os.replace(produced, renamed)
        produced = renamed
    return st.commit(produced)


# --- Subtitles -------------------------------------------------------------------------------

_CUE_TIME = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")


def subtitles_to_text(src: str) -> str:
    with open(src, encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()
    out, previous = [], None
    for line in lines:
        s = line.strip()
        if not s or s == "WEBVTT" or s.isdigit() or _CUE_TIME.search(s) or \
                s.startswith(("NOTE", "STYLE", "REGION")):
            continue
        s = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", s).strip()
        if s and s != previous:
            out.append(s)
            previous = s
    return "\n".join(out) + "\n"


def _subtitle(runner, src, target, st):
    result = st.path(f"out.{target}")
    if target == "txt":
        with open(result, "w", encoding="utf-8") as fh:
            fh.write(subtitles_to_text(src))
        return st.commit(result)
    runner.run(ffmpeg_base() + ["-i", src, result])
    return st.commit(result)


# --- Archives ---------------------------------------------------------------------------------

def _safe_tar_extract(archive: tarfile.TarFile, dest: str):
    try:
        archive.extractall(dest, filter="data")
    except TypeError:  # Python without extraction filters
        root = os.path.realpath(dest)
        for member in archive.getmembers():
            target = os.path.realpath(os.path.join(dest, member.name))
            if not target.startswith(root + os.sep) or member.issym() or member.islnk():
                raise ConversionError(f"Unsafe path in archive: {member.name}")
        archive.extractall(dest)


def extract_archive(runner: Runner, src: str, fmt: str, dest: str):
    os.makedirs(dest, exist_ok=True)
    if fmt == "zip":
        with zipfile.ZipFile(src) as zf:
            zf.extractall(dest)  # zipfile sanitises absolute and ".." paths
    elif fmt in ("tar", "gz"):
        try:
            with tarfile.open(src) as tf:
                _safe_tar_extract(tf, dest)
        except tarfile.ReadError:
            import gzip  # a plain .gz holding a single file
            with gzip.open(src) as gz, open(os.path.join(dest, F.stem(os.path.basename(src))), "wb") as out:
                shutil.copyfileobj(gz, out)
    elif fmt == "rar":
        if tool("unrar") and os.path.basename(tool("unrar")) == "unrar":
            runner.run([tool("unrar"), "x", "-o-", "-y", src, dest + os.sep])
        elif tool("unrar"):
            runner.run([tool("unrar"), "-q", "-o", dest, "-D", src])
        elif tool("7z"):
            runner.run([tool("7z"), "x", "-y", f"-o{dest}", src])
        elif tool("bsdtar"):
            runner.run([tool("bsdtar"), "-xf", src, "-C", dest])
        else:
            raise ConversionError("Install unrar or 7-Zip to open RAR archives")
    else:
        raise ConversionError(f"Cannot extract {fmt}")


def _archive(runner, src, fmt, target, st):
    contents = st.path("contents")
    extract_archive(runner, src, fmt, contents)
    runner.check()
    result = st.path("out." + ("tar.gz" if target == "gz" else target))
    entries = sorted(os.listdir(contents))
    if target == "zip":
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(contents):
                dirs.sort()
                for name in sorted(files):
                    full = os.path.join(root, name)
                    zf.write(full, os.path.relpath(full, contents))
                if not files and not dirs and root != contents:
                    zf.write(root, os.path.relpath(root, contents) + "/")
    else:
        mode = "w:gz" if target == "gz" else "w"
        with tarfile.open(result, mode) as tf:
            for entry in entries:
                tf.add(os.path.join(contents, entry), arcname=entry)
    return st.commit(result)


# --- Readiness report ----------------------------------------------------------------------------

BACKENDS = [
    # (key, human name, Fedora package, what it powers)
    ("ffmpeg", "FFmpeg", "ffmpeg-free", "Audio, video, GIF and subtitle conversions"),
    ("magick", "ImageMagick", "ImageMagick", "Image conversions and photo tools"),
    ("soffice", "LibreOffice", "libreoffice-writer libreoffice-calc libreoffice-impress",
     "Word, spreadsheet, slide and PDF exports"),
    ("pandoc", "Pandoc", "pandoc", "Markdown, HTML and EPUB"),
    ("pdftoppm", "Poppler", "poppler-utils", "PDF pages to images and text"),
    ("qpdf", "QPDF", "qpdf", "Split, merge and organize PDFs"),
    ("gs", "Ghostscript", "ghostscript", "PDF compression"),
    ("exiftool", "ExifTool", "perl-Image-ExifTool", "Editing and removing metadata"),
    ("rsvg-convert", "librsvg", "librsvg2-tools", "SVG input"),
    ("heif-enc", "libheif", "libheif-tools", "HEIC output"),
    ("img2pdf", "img2pdf", "python3-img2pdf", "Lossless image-to-PDF"),
    ("7z", "7-Zip", "7zip", "RAR input"),
    ("tesseract", "Tesseract OCR", "tesseract tesseract-langpack-eng", "Scanned PDF text and photo text redaction"),
]


def readiness() -> list[dict]:
    rows = []
    for key, name, package, purpose in BACKENDS:
        rows.append({"key": key, "name": name, "package": package, "purpose": purpose,
                     "path": tool(key)})
    rows.append({"key": "heic-read", "name": "HEIC decoding", "package": "libheif-freeworld (RPM Fusion)",
                 "purpose": "Opening iPhone HEIC photos",
                 "path": "ImageMagick" if magick_can("heic", "r") else tool("heif-dec") or tool("heif-convert")})
    rows.append({"key": "h264", "name": "H.264 encoder", "package": "ffmpeg (RPM Fusion) or openh264",
                 "purpose": "MP4/MOV/MKV video output",
                 "path": pick_encoder("libx264", "libopenh264")})
    return rows


def backend_for(fmt: str | None, target: str) -> str | None:
    """Name of the primary tool a conversion needs (for availability hints)."""
    cat = F.category(fmt)
    if cat in ("audio", "video") or (cat == "subtitle" and target != "txt"):
        return "ffmpeg"
    if cat == "image":
        return "magick"
    if fmt == "pdf":
        return {"jpg": "pdftoppm", "png": "pdftoppm", "txt": "pdftotext",
                "pptx": "soffice", "xlsx": "soffice"}.get(target, "pandoc")
    if cat == "slides":
        return "soffice"
    if cat == "spreadsheet":
        return None if {fmt, target} <= {"csv", "tsv"} else "soffice"
    if cat == "document":
        if target in ("pdf", "png", "jpg") or fmt in ("docx", "odt", "rtf") and target in ("docx", "odt", "txt", "html"):
            return "soffice"
        return "pandoc"
    if fmt == "rar":
        return "7z" if tool("7z") else "unrar"
    return None


def is_available(fmt: str | None, target: str) -> bool:
    needed = backend_for(fmt, target)
    return needed is None or tool(needed) is not None
