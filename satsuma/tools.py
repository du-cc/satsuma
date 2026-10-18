"""File tools: compress, trim, crop, redact, merge and friends.

Each tool takes the selected files plus a parameter dict (filled in by the
tool's dialog, or defaults for one-shot tools) and returns the paths it made.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess

import cairo

from . import backends as B
from . import formats as F
from .backends import ConversionError, Runner, Staging

# Suffix and output format for single-file tools, as in Tangerine's naming.
SUFFIX = {
    "compress": "Compressed", "removeMetadata": "No Metadata", "editImage": "Edited",
    "annotateImage": "Annotated", "frameImage": "Background", "cropImage": "Cropped",
    "redactImage": "Redacted", "cropVideo": "Cropped", "normalizeAudio": "Normalized",
    "muteVideo": "Muted", "trimVideo": "Trimmed", "trimAudio": "Trimmed",
    "redactAudio": "Bleeped", "changeVideoSpeed": "Speed", "videoSnapshots": "Snapshot",
    "splitVideo": "Clips", "splitPDF": "Pages", "organizePDF": "Organized",
    "redactVideo": "Redacted", "extractArchive": "", "audioToVideo": "Visualizer",
    "audioChannels": "Mono", "createPDF": "Combined", "mergePDF": "Merged",
    "joinVideos": "Joined", "createCollage": "Collage",
}


def output_for(src: str, tool: str, ext: str, params: dict | None = None,
               directory: str | None = None) -> str:
    """``photo Compressed.jpg`` style names; ``ext`` "" makes a folder name."""
    directory = directory or os.path.dirname(os.path.abspath(src))
    suffix = SUFFIX[tool]
    params = params or {}
    if tool == "changeVideoSpeed":
        suffix = f"Speed {params.get('speed', 2):g}x"
    elif tool == "audioChannels":
        suffix = "Mono" if params.get("channels", 1) == 1 else "Stereo"
    elif tool == "removeMetadata" and params.get("mode") == "edit":
        suffix = "Edited Metadata"
    base = F.stem(os.path.basename(src))
    base = f"{base} {suffix}" if suffix else base
    return F.unique_path(directory, base, ext)


def run_tool(runner: Runner, tool: str, files: list[str], params: dict | None = None,
             outdir: str | None = None) -> list[str]:
    params = params or {}
    impl = globals().get(f"_tool_{tool}")
    if impl is None:
        raise ConversionError(f"Unknown tool {tool}")
    if tool in F.MULTI_ONLY_TOOLS:
        return [impl(runner, files, params, outdir)]
    outputs = []
    for path in files:
        runner.check()
        outputs.append(impl(runner, path, params, outdir))
    return outputs


def _ffprobe(path: str) -> dict:
    exe = B.require("ffprobe", "ffmpeg-free")
    out = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", path], capture_output=True, text=True).stdout
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        return {}


def media_info(path: str) -> dict:
    """Duration, size and stream presence for a media file."""
    data = _ffprobe(path)
    info = {"duration": float(data.get("format", {}).get("duration") or 0),
            "width": 0, "height": 0, "has_audio": False, "has_video": False}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not info["has_video"]:
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            info.update(has_video=True, width=int(stream.get("width") or 0),
                        height=int(stream.get("height") or 0))
        elif stream.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def _same_format_video(fmt: str) -> list[str]:
    return B.video_codec(fmt if fmt in F.VIDEO else "mp4")


# --- Compress ---------------------------------------------------------------------------

def _tool_compress(runner, src, params, outdir):
    fmt = F.detect_format(src)
    cat = F.category(fmt)
    if cat == "image":
        magick = B.require("magick", "ImageMagick")
        out = output_for(src, "compress", fmt, params, outdir)
        with Staging(out) as st:
            result = st.path(f"out.{fmt}")
            args = [magick, src + ("[0]" if fmt in ("tiff", "heic", "avif") else ""), "-auto-orient"]
            if params.get("max_side"):
                side = int(params["max_side"])
                args += ["-resize", f"{side}x{side}>"]
            args += ["-strip", "-quality", str(int(params.get("quality", 72)))]
            if fmt == "png":
                args = args[:-2] + ["-define", "png:compression-level=9"]
            runner.run(args + [result])
            return st.commit(result)
    if cat == "audio":
        target = "flac" if fmt in ("wav", "aiff") else fmt
        out = output_for(src, "compress", target, params, outdir)
        with Staging(out) as st:
            result = st.path(f"out.{target}")
            encoder = B._audio_codec(target)[:2]  # ["-c:a", name]
            if target == "flac":
                rate = ["-compression_level", "12"]
            else:
                rate = ["-b:a", f"{int(params.get('bitrate', 128))}k"]
            runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:a:0", "-map_metadata", "0"]
                       + encoder + rate + [result])
            return st.commit(result)
    if cat == "video":
        out = output_for(src, "compress", fmt, params, outdir)
        with Staging(out) as st:
            result = st.path(f"out.{fmt}")
            vf = B.EVEN_SCALE
            if params.get("max_height"):
                h = int(params["max_height"])
                vf = f"scale=-2:'min({h},ih)'"
            crf = int(params.get("crf", 28))
            runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:v:0", "-map", "0:a:0?",
                                          "-vf", vf] + B.video_codec(fmt, crf) + [result])
            return st.commit(result)
    if fmt == "pdf":
        gs = B.require("gs", "ghostscript")
        out = output_for(src, "compress", "pdf", params, outdir)
        with Staging(out) as st:
            result = st.path("out.pdf")
            preset = params.get("preset", "ebook")
            runner.run([gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6",
                        f"-dPDFSETTINGS=/{preset}", "-dNOPAUSE", "-dBATCH", "-dQUIET",
                        "-dSAFER", f"-sOutputFile={result}", src])
            if os.path.getsize(result) >= os.path.getsize(src):
                shutil.copyfile(src, result)  # already as small as it gets
            return st.commit(result)
    raise ConversionError("Nothing to compress for this format")


# --- Metadata ------------------------------------------------------------------------------

META_FIELDS = {
    "image": [("Artist", "Author"), ("Copyright", "Copyright"), ("ImageDescription", "Description")],
    "audio": [("artist", "Artist"), ("album", "Album"), ("title", "Title"), ("comment", "Comments")],
    "video": [("title", "Title"), ("artist", "Author"), ("comment", "Comments")],
    "pdf": [("Title", "Title"), ("Author", "Author"), ("Subject", "Subject")],
}


def _tool_removeMetadata(runner, src, params, outdir):
    fmt = F.detect_format(src)
    cat = F.category(fmt)
    mode = params.get("mode", "remove")
    fields = {k: v for k, v in (params.get("fields") or {}).items() if v is not None}
    out = output_for(src, "removeMetadata", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        if cat in ("audio", "video"):
            args = B.ffmpeg_base() + ["-i", src, "-map", "0", "-c", "copy"]
            if mode == "remove":
                args += ["-map_metadata", "-1", "-map_chapters", "-1"]
                if fmt in ("mp4", "mov", "m4a"):
                    args += ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]
            else:
                for key, value in fields.items():
                    args += ["-metadata", f"{key}={value}"]
            runner.run(args + [result])
            return st.commit(result)
        exiftool = B.tool("exiftool")
        if exiftool:
            if mode == "remove":
                args = [exiftool, "-all=", "-tagsfromfile", "@", "-Orientation", "-ICC_Profile"] \
                    if cat == "image" else [exiftool, "-all="]
            else:
                args = [exiftool] + [f"-{k}={v}" for k, v in fields.items()]
                if params.get("remove_location"):
                    args.append("-gps:all=")
            runner.run(args + ["-o", result, src])
            return st.commit(result)
        if cat == "image" and mode == "remove":
            runner.run([B.require("magick", "ImageMagick"), src, "-auto-orient", "-strip", result])
            return st.commit(result)
        if fmt == "pdf" and mode == "remove" and B.tool("qpdf"):
            runner.run([B.tool("qpdf"), "--remove-info", "--remove-metadata", src, result])
            return st.commit(result)
        raise ConversionError("Install ExifTool to edit metadata (sudo dnf install perl-Image-ExifTool)")


# --- Images -----------------------------------------------------------------------------------

def _tool_editImage(runner, src, params, outdir):
    fmt = F.detect_format(src)
    magick = B.require("magick", "ImageMagick")
    out = output_for(src, "editImage", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run([magick, src + "[0]"] + edit_image_args(params) + [result])
        return st.commit(result)


def edit_image_args(params: dict) -> list[str]:
    """ImageMagick arguments for the Edit Photo sliders (shared with the preview)."""
    args = ["-auto-orient"]
    rotate = int(params.get("rotate", 0)) % 360
    if rotate:
        args += ["-rotate", str(rotate)]
    if params.get("flip"):
        args.append("-flop")
    exposure = float(params.get("exposure", 0))
    contrast = float(params.get("contrast", 0))
    if exposure or contrast:
        args += ["-brightness-contrast", f"{exposure:g}x{contrast:g}"]
    saturation = float(params.get("saturation", 100))
    if params.get("mono"):
        saturation = 0
    if saturation != 100:
        args += ["-modulate", f"100,{saturation:g},100"]
    warmth = float(params.get("warmth", 0))
    if warmth:
        r = 1 + warmth / 400
        b = 1 - warmth / 400
        args += ["-color-matrix", f"{r:.3f} 0 0 0 1 0 0 0 {b:.3f}"]
    detail = float(params.get("detail", 0))
    if detail > 0:
        args += ["-unsharp", f"0x{0.6 + detail / 50:.2f}+{detail / 50:.2f}+0.02"]
    elif detail < 0:
        args += ["-blur", f"0x{-detail / 25:.2f}"]
    vignette = float(params.get("vignette", 0))
    if vignette:
        args += ["-background", "black", "-vignette", f"0x{vignette:g}"]
    return args


def raster_copy(runner: Runner, src: str, st: Staging) -> str:
    """A PNG copy of any image that cairo can read."""
    png = st.path(f"raster-{abs(hash(src))}.png")
    fmt = F.detect_format(src)
    if fmt == "svg" and B.tool("rsvg-convert"):
        runner.run([B.tool("rsvg-convert"), "-f", "png", "-o", png, src])
    else:
        runner.run([B.require("magick", "ImageMagick"), src + "[0]", "-auto-orient",
                    "-colorspace", "sRGB", png])
    return png


def _save_surface(runner: Runner, surface: cairo.ImageSurface, fmt: str, st: Staging) -> str:
    png = st.path("composed.png")
    surface.write_to_png(png)
    if fmt == "png":
        return png
    result = st.path(f"out.{fmt}")
    args = [B.require("magick", "ImageMagick"), png]
    if fmt in ("jpg", "bmp"):
        args += ["-background", "white", "-alpha", "remove", "-alpha", "off", "-quality", "92"]
    runner.run(args + [result])
    return result


def rounded_rect(cr: cairo.Context, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def hex_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _paint_background(cr, w, h, params, st, runner):
    bg = params.get("background", "#ffffff")
    if params.get("background_image"):
        img = cairo.ImageSurface.create_from_png(raster_copy(runner, params["background_image"], st))
        scale = max(w / img.get_width(), h / img.get_height())
        cr.save()
        cr.translate((w - img.get_width() * scale) / 2, (h - img.get_height() * scale) / 2)
        cr.scale(scale, scale)
        cr.set_source_surface(img, 0, 0)
        cr.paint()
        cr.restore()
        return
    if params.get("gradient"):
        g = cairo.LinearGradient(0, 0, w, h)
        g.add_color_stop_rgb(0, *hex_rgb(params["gradient"][0]))
        g.add_color_stop_rgb(1, *hex_rgb(params["gradient"][1]))
        cr.set_source(g)
    else:
        cr.set_source_rgb(*hex_rgb(bg))
    cr.paint()


def _draw_image(cr, img, x, y, w, h, radius, cover=True):
    iw, ih = img.get_width(), img.get_height()
    scale = max(w / iw, h / ih) if cover else min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    cr.save()
    rounded_rect(cr, x, y, w, h, radius) if cover else rounded_rect(
        cr, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, radius)
    cr.clip()
    cr.translate(x + (w - dw) / 2, y + (h - dh) / 2)
    cr.scale(scale, scale)
    cr.set_source_surface(img, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    cr.restore()


ASPECTS = {"1:1": 1.0, "4:5": 4 / 5, "5:4": 5 / 4, "16:9": 16 / 9, "9:16": 9 / 16,
           "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 3 / 2, "2:3": 2 / 3}


def _tool_frameImage(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out_fmt = fmt if fmt in ("png", "jpg", "webp", "tiff") else "png"
    out = output_for(src, "frameImage", out_fmt, params, outdir)
    with Staging(out) as st:
        img = cairo.ImageSurface.create_from_png(raster_copy(runner, src, st))
        iw, ih = img.get_width(), img.get_height()
        pad = float(params.get("padding", 8)) / 100 * max(iw, ih)
        w, h = iw + 2 * pad, ih + 2 * pad
        aspect = ASPECTS.get(params.get("aspect", "original"))
        if aspect:
            if w / h < aspect:
                w = h * aspect
            else:
                h = w / aspect
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(round(w)), int(round(h)))
        cr = cairo.Context(surface)
        _paint_background(cr, w, h, params, st, runner)
        radius = float(params.get("radius", 4)) / 100 * min(iw, ih)
        if params.get("shadow", True):
            cr.save()
            for i in range(12, 0, -1):
                cr.set_source_rgba(0, 0, 0, 0.018)
                rounded_rect(cr, (w - iw) / 2 - i, (h - ih) / 2 - i + pad * 0.08, iw + 2 * i,
                             ih + 2 * i, radius + i)
                cr.fill()
            cr.restore()
        _draw_image(cr, img, (w - iw) / 2, (h - ih) / 2, iw, ih, radius)
        surface.flush()
        return st.commit(_save_surface(runner, surface, out_fmt, st))


def _tool_cropImage(runner, src, params, outdir):
    fmt = F.detect_format(src)
    magick = B.require("magick", "ImageMagick")
    out = output_for(src, "cropImage", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        args = [magick, src + "[0]", "-auto-orient"]
        if "rect" in params:
            x, y, w, h = (int(round(v)) for v in params["rect"])
            args += ["-crop", f"{w}x{h}+{x}+{y}", "+repage"]
        elif params.get("width") and params.get("height"):
            w, h = int(params["width"]), int(params["height"])
            args += ["-resize", f"{w}x{h}^", "-gravity", "center", "-extent", f"{w}x{h}"]
        else:
            ratio = ASPECTS.get(params.get("aspect", "1:1"), 1.0)
            args += ["-gravity", "center", "-crop", f"{ratio:.6f}:1", "+repage"] \
                if _magick7() else _aspect_crop_args(runner, src, ratio)
        runner.run(args + [result])
        return st.commit(result)


def _magick7() -> bool:
    return os.path.basename(B.tool("magick") or "") == "magick"


def _aspect_crop_args(runner, src, ratio):
    out = runner.run([B.tool("magick").replace("convert", "identify"), "-format", "%w %h",
                      src + "[0]"])
    w, h = (int(v) for v in out.split()[:2])
    if w / h > ratio:
        cw, ch = int(h * ratio), h
    else:
        cw, ch = w, int(w / ratio)
    return ["-gravity", "center", "-crop", f"{cw}x{ch}+0+0", "+repage"]


def _tool_redactImage(runner, src, params, outdir):
    fmt = F.detect_format(src)
    magick = B.require("magick", "ImageMagick")
    out = output_for(src, "redactImage", fmt, params, outdir)
    regions = params.get("regions") or []
    if not regions:
        raise ConversionError("Mark at least one area to redact")
    style = params.get("style", "blur")
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        args = [magick, src + "[0]", "-auto-orient"]
        for x, y, w, h in regions:
            x, y, w, h = (max(1, int(round(v))) if i >= 2 else max(0, int(round(v)))
                          for i, v in enumerate((x, y, w, h)))
            if style == "solid":
                args += ["-fill", params.get("color", "#000000"), "-draw",
                         f"rectangle {x},{y} {x + w},{y + h}"]
                continue
            effect = (["-scale", "6%", "-scale", f"{w}x{h}!"] if style == "pixelate"
                      else ["-blur", f"0x{max(8, min(w, h) // 6)}"])
            args += ["(", "+clone", "-crop", f"{w}x{h}+{x}+{y}", "+repage"] + effect + \
                    [")", "-geometry", f"+{x}+{y}", "-composite"]
        runner.run(args + [result])
        return st.commit(result)


def detect_regions(path: str, kind: str) -> list[tuple[int, int, int, int]]:
    """Find faces (OpenCV) or text (Tesseract) locally. Returns image-pixel rects."""
    if kind == "faces":
        try:
            import cv2  # type: ignore
        except ImportError as exc:
            raise ConversionError("Face detection needs OpenCV (sudo dnf install python3-opencv)") from exc
        image = cv2.imread(path)
        if image is None:
            raise ConversionError("Could not read the image for face detection")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cascade_dir = getattr(getattr(cv2, "data", None), "haarcascades", "/usr/share/opencv4/haarcascades/")
        cascade = cv2.CascadeClassifier(os.path.join(cascade_dir, "haarcascade_frontalface_default.xml"))
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(24, 24))
        return [tuple(int(v) for v in f) for f in faces]
    exe = B.tool("tesseract")
    if not exe:
        raise ConversionError("Text detection needs Tesseract (sudo dnf install tesseract)")
    tsv = subprocess.run([exe, path, "-", "tsv"], capture_output=True, text=True).stdout
    boxes = []
    for line in tsv.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) == 12 and cols[11].strip() and cols[10] not in ("-1",) and float(cols[10]) > 40:
            left, top, width, height = (int(c) for c in cols[6:10])
            boxes.append((left - 2, top - 2, width + 4, height + 4))
    return boxes


def _tool_annotateImage(runner, src, params, outdir):
    out = output_for(src, "annotateImage", "png", params, outdir)
    with Staging(out) as st:
        img = cairo.ImageSurface.create_from_png(raster_copy(runner, src, st))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, img.get_width(), img.get_height())
        cr = cairo.Context(surface)
        cr.set_source_surface(img, 0, 0)
        cr.paint()
        draw_annotations(cr, params.get("shapes") or [], max(img.get_width(), img.get_height()))
        surface.flush()
        return st.commit(_save_surface(runner, surface, "png", st))


def draw_annotations(cr: cairo.Context, shapes: list[dict], size: float):
    """Shared by the annotate editor (on screen) and the exporter (full size)."""
    base = max(2.0, size / 300)
    number = 0
    for shape in shapes:
        r, g, b = hex_rgb(shape.get("color", "#ff5a00"))
        kind = shape["kind"]
        x0, y0, x1, y1 = shape["x0"], shape["y0"], shape.get("x1", shape["x0"]), shape.get("y1", shape["y0"])
        width = base * shape.get("weight", 1.5)
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if kind == "arrow":
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()
            angle = math.atan2(y1 - y0, x1 - x0)
            head = width * 4.5
            cr.move_to(x1, y1)
            cr.line_to(x1 - head * math.cos(angle - 0.45), y1 - head * math.sin(angle - 0.45))
            cr.line_to(x1 - head * math.cos(angle + 0.45), y1 - head * math.sin(angle + 0.45))
            cr.close_path()
            cr.fill()
        elif kind in ("rect", "highlight"):
            x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
            rounded_rect(cr, x, y, w, h, width * 1.5)
            if kind == "highlight":
                cr.set_source_rgba(r, g, b, 0.35)
                cr.fill()
            else:
                cr.stroke()
        elif kind == "ellipse":
            w, h = abs(x1 - x0) or 1, abs(y1 - y0) or 1
            cr.save()
            cr.translate((x0 + x1) / 2, (y0 + y1) / 2)
            cr.scale(w / 2, h / 2)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
            cr.stroke()
        elif kind == "pen":
            points = shape.get("points") or []
            if points:
                cr.move_to(*points[0])
                for p in points[1:]:
                    cr.line_to(*p)
                cr.stroke()
        elif kind in ("text", "number"):
            if kind == "number":
                number += 1
                radius = base * 9
                cr.arc(x0, y0, radius, 0, 2 * math.pi)
                cr.fill()
                text, font_size = str(number), radius * 1.15
                cr.set_source_rgb(1, 1, 1)
            else:
                text, font_size = shape.get("text", ""), base * 11
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(font_size)
            ext = cr.text_extents(text)
            if kind == "number":
                cr.move_to(x0 - ext.width / 2 - ext.x_bearing, y0 - ext.height / 2 - ext.y_bearing)
            else:
                cr.move_to(x0, y0)
            cr.show_text(text)
            cr.new_path()


def _tool_createCollage(runner, files, params, outdir):
    order = params.get("order") or files
    first = order[0]
    out_fmt = params.get("format", "jpg")
    directory = outdir or os.path.dirname(os.path.abspath(first))
    out = F.unique_path(directory, "Collage", out_fmt)
    with Staging(out) as st:
        images = [cairo.ImageSurface.create_from_png(raster_copy(runner, p, st)) for p in order]
        runner.check()
        cells = collage_cells(len(images), params.get("layout", "grid"),
                              float(params.get("width", 2400)), images)
        width, height = cells["size"]
        gap = float(params.get("spacing", 2)) / 100 * width
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(width + gap), int(height + gap))
        cr = cairo.Context(surface)
        _paint_background(cr, width + gap, height + gap, params, st, runner)
        radius = float(params.get("radius", 2)) / 100 * width
        for img, (x, y, w, h) in zip(images, cells["rects"]):
            _draw_image(cr, img, x + gap, y + gap, w - gap, h - gap, radius)
        surface.flush()
        return st.commit(_save_surface(runner, surface, out_fmt, st))


def collage_cells(n: int, layout: str, width: float, images=None) -> dict:
    """Cell rectangles (before spacing) for a collage of ``n`` images."""
    rects = []
    if layout == "row":
        ratios = [img.get_width() / img.get_height() for img in images] if images else [1] * n
        height = width / sum(ratios)
        x = 0
        for r in ratios:
            rects.append((x, 0, height * r, height))
            x += height * r
        return {"size": (width, height), "rects": rects}
    if layout == "column":
        ratios = [img.get_height() / img.get_width() for img in images] if images else [1] * n
        col_w = width / 2.5
        y = 0
        for r in ratios:
            rects.append((0, y, col_w, col_w * r))
            y += col_w * r
        return {"size": (col_w, y), "rects": rects}
    if layout == "featured" and n >= 2:
        rest = n - 1
        big = width * 2 / 3
        small_h = big / rest
        rects.append((0, 0, big, big))
        for i in range(rest):
            rects.append((big, i * small_h, width - big, small_h))
        return {"size": (width, big), "rects": rects}
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell = width / cols
    for i in range(n):
        rects.append(((i % cols) * cell, (i // cols) * cell, cell, cell))
    return {"size": (width, rows * cell), "rects": rects}


# --- Audio ----------------------------------------------------------------------------------------

def _keep_audio_codec(fmt: str) -> list[str]:
    return B._audio_codec(fmt)


def _tool_normalizeAudio(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "normalizeAudio", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        lufs = float(params.get("lufs", -16))
        runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:a:0", "-map_metadata", "0",
                                      "-af", f"loudnorm=I={lufs:g}:TP=-1.5:LRA=11",
                                      "-ar", "48000"] + _keep_audio_codec(fmt) + [result])
        return st.commit(result)


def _range_args(params: dict) -> list[str]:
    args = []
    if params.get("start"):
        args += ["-ss", f"{float(params['start']):.3f}"]
    if params.get("end"):
        args += ["-to", f"{float(params['end']):.3f}"]
    return args


def _tool_trimAudio(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "trimAudio", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        af = []
        if params.get("strip_silence"):
            trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.1"
            af = ["-af", f"{trim},areverse,{trim},areverse"]
        runner.run(B.ffmpeg_base() + ["-i", src] + _range_args(params)
                   + ["-map", "0:a:0", "-map_metadata", "0"] + af + _keep_audio_codec(fmt) + [result])
        return st.commit(result)


def _tool_audioChannels(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "audioChannels", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:a:0", "-map_metadata", "0", "-ac",
                                      str(int(params.get("channels", 1)))]
                   + _keep_audio_codec(fmt) + [result])
        return st.commit(result)


def _between(ranges) -> str:
    return "+".join(f"between(t,{float(a):.3f},{float(b):.3f})" for a, b in ranges)


def _tool_redactAudio(runner, src, params, outdir):
    fmt = F.detect_format(src)
    ranges = params.get("ranges") or []
    if not ranges:
        raise ConversionError("Mark at least one range to bleep")
    out = output_for(src, "redactAudio", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        cond = _between(ranges)
        tone = float(params.get("frequency", 1000))
        graph = (f"[0:a]volume=0:enable='{cond}'[muted];"
                 f"sine=f={tone:g}:r=48000,volume=0.3,volume=0:enable='not({cond})'[beep];"
                 "[muted][beep]amix=inputs=2:duration=first:normalize=0[out]")
        runner.run(B.ffmpeg_base() + ["-i", src, "-filter_complex", graph, "-map", "[out]",
                                      "-map_metadata", "0"] + _keep_audio_codec(fmt) + [result])
        return st.commit(result)


VISUALIZER_SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920), "square": (1080, 1080)}


def _tool_audioToVideo(runner, src, params, outdir):
    out = output_for(src, "audioToVideo", "mp4", params, outdir)
    w, h = VISUALIZER_SIZES.get(params.get("aspect", "landscape"), (1920, 1080))
    color = params.get("color", "#ff5a00").lstrip("#")
    with Staging(out) as st:
        result = st.path("out.mp4")
        inputs = ["-i", src]
        if params.get("background_image"):
            bg_png = raster_copy(runner, params["background_image"], st)
            inputs += ["-loop", "1", "-i", bg_png]
            bg = (f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},format=yuv420p[bg]")
        else:
            bg = f"color=c=black:s={w}x{h}:r=30[bg]"
        graph = (f"{bg};[0:a]showwaves=s={w}x{h // 3}:mode=cline:draw=full:rate=30:colors=0x{color},"
                 f"format=yuva420p[wave];[bg][wave]overlay=(W-w)/2:(H-h)/2:shortest=1,"
                 f"format=yuv420p[v]")
        runner.run(B.ffmpeg_base() + inputs + ["-filter_complex", graph, "-map", "[v]",
                                               "-map", "0:a:0", "-shortest"]
                   + B.video_codec("mp4", 23) + [result])
        return st.commit(result)


# --- Video ----------------------------------------------------------------------------------------

def _tool_muteVideo(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "muteVideo", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:v", "-c", "copy", "-an", result])
        return st.commit(result)


def _tool_trimVideo(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "trimVideo", fmt, params, outdir)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run(B.ffmpeg_base() + ["-i", src] + _range_args(params)
                   + ["-map", "0:v:0", "-map", "0:a:0?", "-vf", B.EVEN_SCALE]
                   + _same_format_video(fmt) + [result])
        return st.commit(result)


def _even_rect(rect) -> tuple[int, int, int, int]:
    x, y, w, h = (int(round(v)) for v in rect)
    return x - x % 2, y - y % 2, max(2, w - w % 2), max(2, h - h % 2)


def _tool_cropVideo(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "cropVideo", fmt, params, outdir)
    if "rect" not in params:
        info = media_info(src)
        ratio = ASPECTS.get(params.get("aspect", "1:1"), 1.0)
        w, h = info["width"], info["height"]
        cw, ch = (h * ratio, h) if w / h > ratio else (w, w / ratio)
        params = dict(params, rect=((w - cw) / 2, (h - ch) / 2, cw, ch))
    x, y, w, h = _even_rect(params["rect"])
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run(B.ffmpeg_base() + ["-i", src, "-map", "0:v:0", "-map", "0:a:0?",
                                      "-vf", f"crop={w}:{h}:{x}:{y}", "-c:a", "copy"]
                   + [a for a in _same_format_video(fmt)] + [result])
        return st.commit(result)


def atempo_chain(speed: float) -> str:
    filters = []
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    filters.append(f"atempo={speed:.4f}")
    return ",".join(filters)


def _tool_changeVideoSpeed(runner, src, params, outdir):
    fmt = F.detect_format(src)
    speed = float(params.get("speed", 2))
    out = output_for(src, "changeVideoSpeed", fmt, params, outdir)
    info = media_info(src)
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        args = B.ffmpeg_base() + ["-i", src, "-map", "0:v:0", "-vf",
                                  f"setpts=PTS/{speed:.4f},{B.EVEN_SCALE}"]
        if info["has_audio"]:
            args += ["-map", "0:a:0", "-af", atempo_chain(speed)]
        runner.run(args + _same_format_video(fmt) + [result])
        return st.commit(result)


def _tool_videoSnapshots(runner, src, params, outdir):
    times = sorted(params.get("times") or [0.0])
    base = F.stem(os.path.basename(src))
    directory = outdir or os.path.dirname(os.path.abspath(src))
    if len(times) == 1:
        out = output_for(src, "videoSnapshots", "png", params, outdir)
    else:
        out = F.unique_path(directory, f"{base} Snapshots", "")
    with Staging(out) as st:
        frames = []
        for i, t in enumerate(times, 1):
            frame = st.path(f"{base} {i:02d}.png")
            runner.run(B.ffmpeg_base() + ["-ss", f"{float(t):.3f}", "-i", src, "-frames:v", "1",
                                          frame])
            frames.append(frame)
        if len(frames) == 1:
            return st.commit(frames[0])
        folder = st.path("frames")
        os.makedirs(folder)
        for f in frames:
            os.replace(f, os.path.join(folder, os.path.basename(f)))
        return st.commit(folder)


def _tool_splitVideo(runner, src, params, outdir):
    fmt = F.detect_format(src)
    duration = media_info(src)["duration"]
    points = sorted(p for p in (float(x) for x in params.get("points") or []) if 0 < p < duration)
    if params.get("every"):
        step = float(params["every"])
        points = [step * i for i in range(1, int(duration // step) + 1) if step * i < duration - 0.05]
    if not points:
        raise ConversionError("Add at least one split point")
    edges = [0.0] + points + [duration]
    out = output_for(src, "splitVideo", "", params, outdir)
    base = F.stem(os.path.basename(src))
    with Staging(out) as st:
        folder = st.path("clips")
        os.makedirs(folder)
        for i, (a, b) in enumerate(zip(edges, edges[1:]), 1):
            clip = os.path.join(folder, f"{base} {i:02d}.{fmt}")
            runner.run(B.ffmpeg_base() + ["-i", src, "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                                          "-map", "0:v:0", "-map", "0:a:0?", "-vf", B.EVEN_SCALE]
                       + _same_format_video(fmt) + [clip])
        return st.commit(folder)


def _tool_redactVideo(runner, src, params, outdir):
    fmt = F.detect_format(src)
    regions = params.get("regions") or []
    if not regions:
        raise ConversionError("Mark at least one area to redact")
    start, end = float(params.get("start", 0)), params.get("end")
    enable = f"between(t,{start:.3f},{float(end):.3f})" if end else f"gte(t,{start:.3f})"
    style = params.get("style", "blur")
    out = output_for(src, "redactVideo", fmt, params, outdir)
    chain, last = [], "0:v"
    for i, rect in enumerate(regions):
        x, y, w, h = _even_rect(rect)
        if style == "solid":
            color = params.get("color", "#000000").lstrip("#")
            chain.append(f"[{last}]drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{color}:t=fill:"
                         f"enable='{enable}'[v{i}]")
        else:
            effect = (f"scale=max(2\\,iw/16):-2,scale={w}:{h}:flags=neighbor" if style == "pixelate"
                      else f"boxblur={max(4, min(w, h) // 8)}:2")
            chain.append(f"[{last}]split[b{i}][s{i}];[s{i}]crop={w}:{h}:{x}:{y},{effect}[e{i}];"
                         f"[b{i}][e{i}]overlay={x}:{y}:enable='{enable}'[v{i}]")
        last = f"v{i}"
    with Staging(out) as st:
        result = st.path(f"out.{fmt}")
        runner.run(B.ffmpeg_base() + ["-i", src, "-filter_complex", ";".join(chain),
                                      "-map", f"[{last}]", "-map", "0:a:0?", "-c:a", "copy"]
                   + _same_format_video(fmt) + [result])
        return st.commit(result)


def _tool_joinVideos(runner, files, params, outdir):
    order = params.get("order") or files
    infos = [media_info(p) for p in order]
    w = infos[0]["width"] or 1280
    h = infos[0]["height"] or 720
    w, h = w - w % 2, h - h % 2
    directory = outdir or os.path.dirname(os.path.abspath(order[0]))
    out = F.unique_path(directory, f"{F.stem(os.path.basename(order[0]))} Joined", "mp4")
    inputs, graph, pairs = [], [], []
    for i, path in enumerate(order):
        inputs += ["-i", path]
    extra = len(order)
    for i, info in enumerate(infos):
        graph.append(f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]")
        if info["has_audio"]:
            graph.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            inputs += ["-f", "lavfi", "-t", f"{info['duration'] or 1:.3f}",
                       "-i", "anullsrc=r=48000:cl=stereo"]
            graph.append(f"[{extra}:a]aformat=channel_layouts=stereo[a{i}]")
            extra += 1
        pairs.append(f"[v{i}][a{i}]")
    graph.append("".join(pairs) + f"concat=n={len(order)}:v=1:a=1[v][a]")
    with Staging(out) as st:
        result = st.path("out.mp4")
        runner.run(B.ffmpeg_base() + inputs + ["-filter_complex", ";".join(graph),
                                               "-map", "[v]", "-map", "[a]"]
                   + B.video_codec("mp4") + [result])
        return st.commit(result)


# --- PDF ----------------------------------------------------------------------------------------------

def pdf_page_count(path: str) -> int:
    if B.tool("qpdf"):
        out = subprocess.run([B.tool("qpdf"), "--show-npages", path], capture_output=True,
                             text=True).stdout.strip()
        if out.isdigit():
            return int(out)
    if B.tool("pdfinfo"):
        out = subprocess.run([B.tool("pdfinfo"), path], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[1])
    return 0


def _tool_splitPDF(runner, src, params, outdir):
    out = output_for(src, "splitPDF", "", params, outdir)
    base = F.stem(os.path.basename(src))
    with Staging(out) as st:
        folder = st.path("pages")
        os.makedirs(folder)
        if B.tool("qpdf"):
            runner.run([B.tool("qpdf"), "--split-pages", src, os.path.join(folder, f"{base} %d.pdf")])
        else:
            runner.run([B.require("pdfseparate", "poppler-utils"), src,
                        os.path.join(folder, f"{base} %d.pdf")])
        return st.commit(folder)


def _tool_mergePDF(runner, files, params, outdir):
    order = params.get("order") or files
    directory = outdir or os.path.dirname(os.path.abspath(order[0]))
    out = F.unique_path(directory, f"{F.stem(os.path.basename(order[0]))} Merged", "pdf")
    with Staging(out) as st:
        result = st.path("out.pdf")
        if B.tool("qpdf"):
            runner.run([B.tool("qpdf"), "--empty", "--pages", *order, "--", result])
        else:
            runner.run([B.require("pdfunite", "poppler-utils"), *order, result])
        return st.commit(result)


def _tool_organizePDF(runner, src, params, outdir):
    qpdf = B.require("qpdf", "qpdf")
    pages = params.get("pages")  # [(1-based source page, rotation degrees)]
    if not pages:
        pages = [(i, 0) for i in range(1, pdf_page_count(src) + 1)]
    out = output_for(src, "organizePDF", "pdf", params, outdir)
    with Staging(out) as st:
        result = st.path("out.pdf")
        selection = ",".join(str(p) for p, _ in pages)
        args = [qpdf, "--empty", "--pages", src, selection, "--"]
        for index, (_, rotation) in enumerate(pages, 1):
            if rotation % 360:
                args.append(f"--rotate=+{rotation % 360}:{index}")
        runner.run(args + [result])
        return st.commit(result)


def _tool_createPDF(runner, files, params, outdir):
    order = params.get("order") or files
    directory = outdir or os.path.dirname(os.path.abspath(order[0]))
    out = F.unique_path(directory, f"{F.stem(os.path.basename(order[0]))} Combined", "pdf")
    with Staging(out) as st:
        result = st.path("out.pdf")
        pages = []
        for i, path in enumerate(order):
            fmt = F.detect_format(path)
            if fmt in ("jpg", "png") and B.tool("img2pdf"):
                pages.append(path)
            else:
                page = st.path(f"page-{i:04d}.png" if fmt != "jpg" else f"page-{i:04d}.jpg")
                if fmt == "svg" and B.tool("rsvg-convert"):
                    runner.run([B.tool("rsvg-convert"), "-f", "png", "-o", page, path])
                else:
                    runner.run([B.require("magick", "ImageMagick"), path + "[0]", "-auto-orient",
                                "-background", "white", "-alpha", "remove", "-alpha", "off", page])
                pages.append(page)
        if B.tool("img2pdf"):
            runner.run([B.tool("img2pdf"), *pages, "-o", result])
        else:
            runner.run([B.require("magick", "ImageMagick"), *pages, result])
        return st.commit(result)


# --- Archives -----------------------------------------------------------------------------------------

def _tool_extractArchive(runner, src, params, outdir):
    fmt = F.detect_format(src)
    out = output_for(src, "extractArchive", "", params, outdir)
    with Staging(out) as st:
        folder = st.path("contents")
        B.extract_archive(runner, src, fmt, folder)
        return st.commit(folder)
