import os
import tempfile
import unittest

from satsuma import formats as F


class FormatMatrixTests(unittest.TestCase):
    def test_sources_and_pairs(self):
        sources = sorted({s for s in F.ALL_KNOWN if F.conversion_targets(s)})
        pairs = sum(len(F.conversion_targets(s)) for s in sources)
        # Tangerine's macOS matrix has 275 pairs. Linux drops Pages/RTFD/text
        # clippings and adds ODT/ODP/HTML inputs, which nets out at 268.
        self.assertEqual(pairs, 268)
        self.assertNotIn("pages", F.ALL_KNOWN)

    def test_targets_never_include_self_or_input_only(self):
        for fmt in F.ALL_KNOWN:
            targets = F.conversion_targets(fmt)
            self.assertNotIn(fmt, targets)
            self.assertFalse(set(targets) & F.INPUT_ONLY, fmt)

    def test_wheel_orders_match_tangerine(self):
        self.assertEqual(F.conversion_targets("wav"),
                         ["mp3", "m4a", "flac", "ogg", "opus", "aiff", "wma"])
        self.assertEqual(F.conversion_targets("mov"),
                         ["mp4", "mkv", "webm", "avi", "wmv", "gif", "mp3"])
        self.assertEqual(F.conversion_targets("xls"), ["xlsx", "ods", "csv", "tsv", "pdf"])
        self.assertEqual(F.conversion_targets("gz"), ["zip", "tar"])

    def test_detect_format(self):
        self.assertEqual(F.detect_format("/x/Photo.JPEG"), "jpg")
        self.assertEqual(F.detect_format("/x/a.tar.gz"), "gz")
        self.assertEqual(F.detect_format("/x/a.tgz"), "gz")
        self.assertIsNone(F.detect_format("/x/a.unknown"))
        self.assertIsNone(F.detect_format("/x/README"))

    def test_unique_names_follow_tangerine(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "song.wav")
            self.assertEqual(os.path.basename(F.conversion_output(src, "mp3")), "song.mp3")
            open(os.path.join(d, "song.mp3"), "w").close()
            self.assertEqual(os.path.basename(F.conversion_output(src, "mp3")), "song 2.mp3")
            self.assertEqual(os.path.basename(F.conversion_output(os.path.join(d, "a.zip"), "gz")),
                             "a.tar.gz")

    def test_tools(self):
        self.assertEqual(F.tool_targets(["mp3"]),
                         ["compress", "removeMetadata", "normalizeAudio", "audioToVideo",
                          "trimAudio", "audioChannels", "redactAudio"])
        self.assertEqual(F.tool_targets(["jpg", "png"]),
                         ["compress", "removeMetadata", "cropImage", "createPDF", "createCollage"])
        self.assertEqual(F.tool_targets(["pdf", "pdf"]),
                         ["compress", "removeMetadata", "splitPDF", "mergePDF"])
        self.assertNotIn("createPDF", F.tool_targets(["jpg"]))
        self.assertEqual(F.tool_targets(["jpg", "mp3"]), ["compress", "removeMetadata"])
        self.assertEqual(F.tool_targets([None]), [])
        self.assertEqual(len(F.TOOLS), 26)
        self.assertEqual(len(F.ACTIONS), 6)

    def test_selection_targets(self):
        self.assertEqual(F.selection_targets(["jpg", "jpg"]), F.conversion_targets("jpg"))
        mixed = F.selection_targets(["jpg", "png"])
        self.assertIn("jpg", mixed)
        self.assertIn("webp", mixed)
        self.assertEqual(F.selection_targets(["jpg", "mp3"]), [])
        self.assertEqual(F.selection_targets([None]), [])


if __name__ == "__main__":
    unittest.main()
