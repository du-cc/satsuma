import math
import os
import subprocess
import tempfile
import unittest

from satsuma import integration as I
from satsuma.wheel import INNER, OUTER, hit_index, petal_angle


class WheelGeometryTests(unittest.TestCase):
    def test_first_petal_is_at_the_top_and_order_is_clockwise(self):
        r = (INNER + OUTER) / 2
        for count in (2, 4, 6, 7, 9):
            for i in range(count):
                a = petal_angle(i, count)
                x, y = math.cos(a) * r, -math.sin(a) * r
                self.assertEqual(hit_index(x, y, count), i)
        self.assertEqual(hit_index(0, -r, 7), 0)          # top
        self.assertEqual(hit_index(r * 0.7, -r * 0.7, 8), 1)  # next one is to the right

    def test_three_items_start_on_the_left(self):
        r = (INNER + OUTER) / 2
        self.assertEqual(hit_index(-r, 0, 3), 0)

    def test_hole_rim_and_gaps_miss(self):
        self.assertIsNone(hit_index(0, 0, 6))
        self.assertIsNone(hit_index(0, -(OUTER + 5), 6))
        r = (INNER + OUTER) / 2
        boundary = petal_angle(0, 6) - math.pi / 6  # between petals 0 and 1
        self.assertIsNone(hit_index(math.cos(boundary) * r, -math.sin(boundary) * r, 6))
        self.assertIsNone(hit_index(10, 10, 0))


class ShortcutScriptTests(unittest.TestCase):
    def test_script_quotes_paths_for_gdbus_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            bindir = os.path.join(d, "bin")
            os.makedirs(bindir)
            log = os.path.join(d, "log")
            # Fake gdbus records the GVariant text; fake satsuma records the fallback.
            with open(os.path.join(bindir, "gdbus"), "w") as fh:
                fh.write(f"#!/bin/bash\nprintf '%s\\n' \"$@\" > {log}.gdbus\nexit 1\n")
            with open(os.path.join(bindir, "satsuma"), "w") as fh:
                fh.write(f"#!/bin/bash\nprintf '%s\\n' \"$@\" > {log}.fallback\n")
            for name in ("gdbus", "satsuma"):
                os.chmod(os.path.join(bindir, name), 0o755)
            script = os.path.join(d, "wheel")
            with open(script, "w") as fh:
                fh.write(I.SCRIPT.replace("@APP_ID@", "a.b.C").replace("@OBJECT_PATH@", "/a/b/C")
                         .replace("@COMMAND@", "satsuma"))
            os.chmod(script, 0o755)
            paths = ["/tmp/it's here.png", "/tmp/back\\slash.jpg"]
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                       NAUTILUS_SCRIPT_SELECTED_FILE_PATHS="\n".join(paths) + "\n",
                       XDG_ACTIVATION_TOKEN="tok'en")
            subprocess.run([script], env=env, check=True)
            with open(log + ".gdbus") as fh:
                args = fh.read()
            self.assertIn(r"[<@as ['/tmp/it\'s here.png', '/tmp/back\\slash.jpg']>]", args)
            self.assertIn(r"'activation-token': <'tok\'en'>", args)
            with open(log + ".fallback") as fh:
                self.assertEqual(fh.read().splitlines(), ["--wheel"] + paths)


if __name__ == "__main__":
    unittest.main()
