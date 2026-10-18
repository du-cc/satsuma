"""Generate one small sample file per input format for integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile

from satsuma import backends as B


def _run(*cmd, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd, capture_output=True)


def make_samples(root: str) -> dict[str, str]:
    os.makedirs(root, exist_ok=True)
    s: dict[str, str] = {}
    ff = [B.tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    magick = B.tool("magick")

    png = os.path.join(root, "photo.png")
    _run(magick, "-size", "320x200", "gradient:orange-white", "-fill", "black",
         "-draw", "circle 160,100 160,140", png)
    s["png"] = png
    for fmt in ("jpg", "webp", "tiff", "bmp"):
        path = os.path.join(root, f"photo.{fmt}")
        _run(magick, png, path)
        s[fmt] = path
    svg = os.path.join(root, "logo.svg")
    with open(svg, "w") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
                 '<circle cx="32" cy="32" r="28" fill="#ff5a00"/></svg>')
    s["svg"] = svg
    for fmt in ("heic", "avif"):
        path = os.path.join(root, f"photo.{fmt}")
        try:
            if B.magick_can(fmt, "w"):
                _run(magick, png, path)
            elif B.tool("heif-enc"):
                _run(B.tool("heif-enc"), *(["-A"] if fmt == "avif" else []), png, "-o", path)
            else:
                continue
            s[fmt] = path
        except subprocess.CalledProcessError:
            pass

    wav = os.path.join(root, "tone.wav")
    _run(*ff, "-f", "lavfi", "-i", "sine=frequency=440:duration=2", wav)
    s["wav"] = wav
    for fmt, codec in (("mp3", "libmp3lame"), ("m4a", "aac"), ("flac", "flac"),
                       ("ogg", "libvorbis"), ("opus", "libopus"), ("aiff", "pcm_s16be"),
                       ("wma", "wmav2")):
        path = os.path.join(root, f"tone.{fmt}")
        try:
            _run(*ff, "-i", wav, "-c:a", codec, path)
            s[fmt] = path
        except subprocess.CalledProcessError:
            pass

    mp4 = os.path.join(root, "clip.mp4")
    _run(*ff, "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=2", "-shortest",
         "-pix_fmt", "yuv420p", *B.h264_args(28)[:2], mp4)
    s["mp4"] = mp4
    for fmt, args in (("mov", ["-c", "copy"]), ("mkv", ["-c", "copy"]),
                      ("webm", ["-c:v", "libvpx-vp9", "-c:a", "libopus", "-deadline", "realtime"]),
                      ("avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame"]),
                      ("wmv", ["-c:v", "wmv2", "-c:a", "wmav2"])):
        path = os.path.join(root, f"clip.{fmt}")
        try:
            _run(*ff, "-i", mp4, *args, path)
            s[fmt] = path
        except subprocess.CalledProcessError:
            pass
    gif = os.path.join(root, "anim.gif")
    _run(*ff, "-i", mp4, "-vf", "fps=5,scale=160:-1", gif)
    s["gif"] = gif

    md = os.path.join(root, "notes.md")
    with open(md, "w") as fh:
        fh.write("# Field notes\n\nA *small* paragraph.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    s["md"] = md
    txt = os.path.join(root, "plain.txt")
    with open(txt, "w") as fh:
        fh.write("Plain text line one.\nLine two <with> & symbols.\n\nSecond paragraph.\n")
    s["txt"] = txt
    for fmt in ("docx", "odt", "html", "epub", "rtf", "pptx"):
        path = os.path.join(root, f"notes.{fmt}")
        _run(B.tool("pandoc"), md, "-s", "--metadata", "title=Notes", "-o", path)
        s[fmt] = path
    lo = os.path.join(root, "lo")
    os.makedirs(lo, exist_ok=True)
    pdf = B.soffice(B.Runner(), s["docx"], "pdf", lo)
    shutil.move(pdf, os.path.join(root, "notes.pdf"))
    s["pdf"] = os.path.join(root, "notes.pdf")
    for fmt, flt in (("ppt", "ppt"), ("odp", "odp")):
        produced = B.soffice(B.Runner(), s["pptx"], flt, lo)
        shutil.move(produced, os.path.join(root, f"notes.{fmt}"))
        s[fmt] = os.path.join(root, f"notes.{fmt}")

    csv_path = os.path.join(root, "table.csv")
    with open(csv_path, "w") as fh:
        fh.write("name,qty\napple,3\n\"pear, green\",5\n")
    s["csv"] = csv_path
    tsv = os.path.join(root, "table.tsv")
    with open(tsv, "w") as fh:
        fh.write("name\tqty\napple\t3\npear\t5\n")
    s["tsv"] = tsv
    for fmt, flt in (("xlsx", B.SHEET_FILTERS["xlsx"]), ("ods", "ods"),
                     ("xls", "xls:MS Excel 97")):
        produced = B.soffice(B.Runner(), csv_path, flt, lo, infilter="CSV:44,34,76,1")
        shutil.move(produced, os.path.join(root, f"table.{fmt}"))
        s[fmt] = os.path.join(root, f"table.{fmt}")

    srt = os.path.join(root, "subs.srt")
    with open(srt, "w") as fh:
        fh.write("1\n00:00:01,000 --> 00:00:02,500\nHello <i>there</i>\n\n"
                 "2\n00:00:03,000 --> 00:00:04,000\nSecond line\n")
    s["srt"] = srt
    vtt = os.path.join(root, "subs.vtt")
    _run(*ff, "-i", srt, vtt)
    s["vtt"] = vtt

    payload = os.path.join(root, "payload")
    os.makedirs(os.path.join(payload, "inner"), exist_ok=True)
    with open(os.path.join(payload, "inner", "a.txt"), "w") as fh:
        fh.write("archive member\n")
    zpath = os.path.join(root, "bundle.zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(os.path.join(payload, "inner", "a.txt"), "inner/a.txt")
    s["zip"] = zpath
    for fmt, mode in (("tar", "w"), ("gz", "w:gz")):
        path = os.path.join(root, "bundle.tar" if fmt == "tar" else "bundle.tar.gz")
        with tarfile.open(path, mode) as tf:
            tf.add(os.path.join(payload, "inner"), arcname="inner")
        s[fmt] = path
    if B.tool("rar"):
        rar = os.path.join(root, "bundle.rar")
        _run(B.tool("rar"), "a", rar, "inner", cwd=payload)
        s["rar"] = rar
    shutil.rmtree(lo, ignore_errors=True)
    return s
