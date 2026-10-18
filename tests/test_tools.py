"""Exercise every file tool on generated samples."""

import os
import shutil
import tempfile
import unittest
import zipfile

from satsuma import backends as B
from satsuma import formats as F
from satsuma import tools as T
from tests.samples import make_samples


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="satsuma-tools-")
        cls.s = make_samples(os.path.join(cls.root, "in"))

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get("SATSUMA_KEEP"):
            shutil.rmtree(cls.root, ignore_errors=True)

    def run_tool(self, tool, files, params=None):
        outdir = os.path.join(self.root, "out", tool)
        os.makedirs(outdir, exist_ok=True)
        outputs = T.run_tool(B.Runner(), tool, files, params or {}, outdir)
        for path in outputs:
            self.assertTrue(os.path.exists(path), path)
        self.assertEqual([n for n in os.listdir(outdir) if n.startswith(".satsuma-")], [])
        return outputs

    def test_catalogue_has_an_implementation_for_every_tool(self):
        for tool in F.TOOLS:
            self.assertTrue(hasattr(T, f"_tool_{tool}"), tool)

    # Images
    def test_image_tools(self):
        jpg = self.s["jpg"]
        out = self.run_tool("compress", [jpg], {"quality": 50, "max_side": 100})
        self.assertTrue(out[0].endswith("photo Compressed.jpg"))
        self.run_tool("removeMetadata", [jpg])
        self.run_tool("removeMetadata", [jpg], {"mode": "edit", "fields": {"Artist": "Me"},
                                                "remove_location": True})
        self.run_tool("editImage", [jpg], {"exposure": 10, "contrast": 5, "saturation": 120,
                                           "warmth": 20, "detail": 20, "rotate": 90})
        self.run_tool("annotateImage", [jpg], {"shapes": [
            {"kind": "arrow", "x0": 10, "y0": 10, "x1": 100, "y1": 80},
            {"kind": "rect", "x0": 20, "y0": 20, "x1": 60, "y1": 60},
            {"kind": "ellipse", "x0": 120, "y0": 20, "x1": 200, "y1": 90},
            {"kind": "text", "x0": 30, "y0": 150, "text": "Hello"},
            {"kind": "number", "x0": 250, "y0": 50},
            {"kind": "pen", "x0": 0, "y0": 0, "points": [(5, 5), (40, 30), (80, 10)]},
        ]})
        self.run_tool("frameImage", [jpg], {"aspect": "1:1", "gradient": ["#ff5a00", "#ffd000"]})
        self.run_tool("cropImage", [jpg], {"rect": (10, 10, 100, 80)})
        self.run_tool("cropImage", [jpg, self.s["png"]], {"aspect": "1:1"})
        self.run_tool("cropImage", [jpg], {"width": 64, "height": 64})
        for style in ("blur", "pixelate", "solid"):
            self.run_tool("redactImage", [jpg], {"regions": [(20, 20, 80, 60)], "style": style})
        self.run_tool("createPDF", [jpg, self.s["png"], self.s["svg"]])
        for layout in ("grid", "row", "column", "featured"):
            self.run_tool("createCollage", [jpg, self.s["png"], self.s["webp"]],
                          {"layout": layout, "width": 600})

    # Audio
    def test_audio_tools(self):
        mp3, wav = self.s["mp3"], self.s["wav"]
        out = self.run_tool("compress", [wav])
        self.assertTrue(out[0].endswith(".flac"))
        self.run_tool("compress", [mp3], {"bitrate": 64})
        self.run_tool("removeMetadata", [mp3])
        self.run_tool("removeMetadata", [mp3], {"mode": "edit", "fields": {"artist": "Me"}})
        self.run_tool("normalizeAudio", [mp3])
        self.run_tool("trimAudio", [mp3], {"start": 0.2, "end": 1.5, "strip_silence": True})
        out = self.run_tool("audioChannels", [mp3], {"channels": 2})
        self.assertIn("Stereo", out[0])
        self.run_tool("redactAudio", [mp3], {"ranges": [(0.2, 0.6), (1.0, 1.3)]})
        self.run_tool("audioToVideo", [mp3], {"aspect": "square"})
        self.run_tool("audioToVideo", [mp3], {"aspect": "portrait", "background_image": self.s["jpg"]})

    # Video
    def test_video_tools(self):
        mp4, mov = self.s["mp4"], self.s["mov"]
        self.run_tool("compress", [mp4], {"crf": 32, "max_height": 120})
        self.run_tool("removeMetadata", [mp4])
        self.run_tool("muteVideo", [mp4])
        self.run_tool("trimVideo", [mov], {"start": 0.5, "end": 1.5})
        self.run_tool("cropVideo", [mp4], {"rect": (11, 11, 101, 81)})
        self.run_tool("cropVideo", [mp4], {"aspect": "9:16"})
        out = self.run_tool("changeVideoSpeed", [mp4], {"speed": 0.5})
        self.assertIn("Speed 0.5x", out[0])
        self.run_tool("changeVideoSpeed", [mp4], {"speed": 4})
        self.run_tool("videoSnapshots", [mp4], {"times": [0.5]})
        out = self.run_tool("videoSnapshots", [mp4], {"times": [0.2, 1.0]})
        self.assertEqual(len(os.listdir(out[0])), 2)
        out = self.run_tool("splitVideo", [mp4], {"points": [0.7, 1.4]})
        self.assertEqual(len(os.listdir(out[0])), 3)
        for style in ("blur", "pixelate", "solid"):
            self.run_tool("redactVideo", [mp4], {"regions": [(20, 20, 80, 60)], "style": style,
                                                 "start": 0.2, "end": 1.2})
        self.run_tool("joinVideos", [mp4, self.s["mkv"], self.s["webm"]])
        self.run_tool("removeMetadata", [self.s["gif"]])

    # PDF and archives
    def test_pdf_and_archive_tools(self):
        pdf = self.s["pdf"]
        two_pages = self.run_tool("mergePDF", [pdf, pdf])[0]
        self.assertEqual(T.pdf_page_count(two_pages), 2)
        self.run_tool("compress", [pdf], {"preset": "screen"})
        self.run_tool("removeMetadata", [pdf])
        out = self.run_tool("splitPDF", [two_pages])
        self.assertEqual(len(os.listdir(out[0])), 2)
        out = self.run_tool("organizePDF", [two_pages], {"pages": [(2, 90), (1, 0), (1, 180)]})
        self.assertEqual(T.pdf_page_count(out[0]), 3)
        for fmt in ("zip", "tar", "gz"):
            out = self.run_tool("extractArchive", [self.s[fmt]])
            self.assertTrue(os.path.exists(os.path.join(out[0], "inner", "a.txt")))

    def test_zip_slip_is_contained(self):
        evil = os.path.join(self.root, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../../escape.txt", "x")
        out = self.run_tool("extractArchive", [evil])
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape.txt")))
        self.assertTrue(os.path.exists(os.path.join(out[0], "escape.txt")))


if __name__ == "__main__":
    unittest.main()
