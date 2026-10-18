"""Run every supported conversion against generated samples.

Slow (LibreOffice starts for each office conversion). Pairs whose back end
is not installed are skipped and reported.
"""

import os
import shutil
import sys
import tempfile
import unittest

from satsuma import backends as B
from satsuma import formats as F
from tests.samples import make_samples


class ConversionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="satsuma-test-")
        cls.samples = make_samples(os.path.join(cls.root, "in"))

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get("SATSUMA_KEEP"):
            shutil.rmtree(cls.root, ignore_errors=True)

    def test_every_pair(self):
        only = os.environ.get("SATSUMA_ONLY")
        failures, skipped, done = [], [], 0
        for fmt in sorted(self.samples):
            if only and fmt not in only.split(","):
                continue
            src = self.samples[fmt]
            for target in F.conversion_targets(fmt):
                if not B.is_available(fmt, target):
                    skipped.append(f"{fmt}->{target}")
                    continue
                outdir = os.path.join(self.root, "out", f"{fmt}-{target}")
                os.makedirs(outdir, exist_ok=True)
                out = F.conversion_output(src, target, outdir)
                try:
                    final = B.convert(B.Runner(), src, target, out)
                    self.assertTrue(os.path.exists(final))
                    if os.path.isfile(final):
                        self.assertGreater(os.path.getsize(final), 0)
                    leftovers = [n for n in os.listdir(outdir) if n.startswith(".satsuma-")]
                    self.assertEqual(leftovers, [])
                    done += 1
                except Exception as exc:  # noqa: BLE001 - collect all failures
                    failures.append(f"{fmt}->{target}: {exc}")
        missing_inputs = sorted(s for s in F.ALL_KNOWN
                                if F.conversion_targets(s) and s not in self.samples)
        print(f"\n{done} conversions passed; skipped (backend missing): {skipped}; "
              f"no sample for: {missing_inputs}", file=sys.stderr)
        if failures:
            self.fail("\n".join(failures))


if __name__ == "__main__":
    unittest.main()
