"""Regression tests: tall vertical blocks read in column chunks, OCR/note text cleaning.

Fixtures are the text blocks of posts 14207, 14212, 14213 (256-colour PNGs; the column cuts move
by <= 2 px against the originals). Whole, these blocks were OCRed with columns missing or out of
order and, on 14207, a ♦♩♦♩… loop up to max_tokens. They are crops of the booru's images, so they
are NOT in git (tests/fixtures/ is ignored): recreate them with tests/make_fixtures.py; the tests
that need them are skipped when they are missing.

Run inside the image, with the code mounted (no pytest there):
  docker run --rm -v "$PWD/translator:/work" -w /work --entrypoint python szuru-translate \\
      -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import colorsplit  # noqa: E402
import pipeline  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# post -> number of text columns in its block (counted on the image)
BLOCKS = {14207: 7, 14212: 8, 14213: 6}


def block(pid: int) -> Image.Image:
    return Image.open(FIXTURES / f"block_{pid}.png").convert("RGB")


needs_fixtures = unittest.skipUnless(
    all((FIXTURES / f"block_{pid}.png").exists() for pid in BLOCKS),
    "fixtures missing: run tests/make_fixtures.py <posts dir>")


@needs_fixtures
class ColumnsTest(unittest.TestCase):
    def test_column_count_and_order(self):
        for pid, n in BLOCKS.items():
            with self.subTest(post=pid):
                img = block(pid)
                cols = colorsplit.columns(img, [0, 0, img.width, img.height])
                self.assertIsNotNone(cols)
                self.assertEqual(len(cols), n)
                # right to left, contiguous, covering the whole block: no column can fall out
                self.assertEqual(cols[0][1], img.width)
                self.assertEqual(cols[-1][0], 0)
                for (lo_r, _), (_, hi_l) in zip(cols, cols[1:]):
                    self.assertEqual(hi_l, lo_r)
                widths = [hi - lo for lo, hi in cols]
                self.assertLess(max(widths[1:-1]) / min(widths[1:-1]), 1.6)

    def test_page_coordinates(self):
        img = block(14213)
        page = Image.new("RGB", (img.width + 500, img.height + 300), "white")
        page.paste(img, (400, 200))
        cols = colorsplit.columns(page, [400, 200, 400 + img.width, 200 + img.height])
        self.assertEqual(len(cols), BLOCKS[14213])
        self.assertEqual((cols[0][1], cols[-1][0]), (400 + img.width, 400))

    def test_not_columns(self):
        img = block(14207)
        cases = {
            "blank": Image.new("RGB", (400, 900), "white"),
            "landscape (rotated: horizontal rows)": img.transpose(Image.Transpose.ROTATE_90),
            "two columns": img.crop((img.width - 240, 0, img.width, img.height)),
        }
        for name, im in cases.items():
            with self.subTest(name):
                cols = colorsplit.columns(im, [0, 0, im.width, im.height])
                self.assertTrue(cols is None or len(cols) < 5, cols)


class FakeOcr:
    """Records the crops (page x range) it is called with; answers with the column numbers."""

    def __init__(self, cols):
        self.cols, self.calls, self.last_upscale = cols, [], None

    def __call__(self, crop, glyph=None):
        self.calls.append((crop.info["x"], glyph))
        lo, hi = crop.info["x"]
        inside = [i for i, (a, b) in enumerate(self.cols) if a >= lo - 1 and b <= hi + 1]
        return "".join(f"[{i}]" for i in inside), {"completion_tokens": 5, "prompt_tokens": 100}


@needs_fixtures
class ChunkedOcrTest(unittest.TestCase):
    def run_note(self, pid: int, per_call: int = 3):
        img = block(pid)
        cols = colorsplit.columns(img, [0, 0, img.width, img.height])
        orig_crop = img.crop

        def crop(box):  # tag each crop with its page x range for FakeOcr
            c = orig_crop(box)
            c.info["x"] = (box[0], box[2])
            return c

        img.crop = crop
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=per_call, ocr_column_split_min=5)
        pl.ocr = FakeOcr(cols)
        note = {"text_box": [0, 0, img.width, img.height]}
        pl._ocr_note(img, note)
        return cols, pl.ocr.calls, note

    def test_every_column_once_right_to_left(self):
        for pid, n in BLOCKS.items():
            with self.subTest(post=pid):
                cols, calls, note = self.run_note(pid)
                self.assertEqual(len(calls), -(-n // 3))
                # columns out == columns in, each once, in reading order (right to left)
                self.assertEqual(note["source"].replace("\n", ""), "".join(f"[{i}]" for i in range(n)))
                self.assertEqual(note["ocr_chunks"], [n, len(calls)])
                self.assertEqual(note["ocr_usage"]["completion_tokens"], 5 * len(calls))
                xs = [x for x, _ in calls]
                self.assertEqual(xs, sorted(xs, reverse=True))
                self.assertTrue(all(g for _, g in calls))  # chunks pass the pitch: max_tokens capped

    def test_disabled(self):
        _, calls, note = self.run_note(14213, per_call=0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("ocr_chunks", note)


class CleanTextTest(unittest.TestCase):
    def test_loop_from_14207(self):
        raw = "胸が熱い……！♡だよー♡！" + "♦♩" * 60
        self.assertEqual(pipeline.clean_text(raw), "胸が熱い……！♡だよー♡！")

    def test_keeps_real_text(self):
        for text in ("だめだめだめだめっ!!", "もー!…!?!? あたい今なんて…?", "身体がおかしい…!?♡",
                     "んんんんっっ!?♡♡", "Something's coming! ♡", "ああああああっ"):
            with self.subTest(text):
                self.assertEqual(pipeline.clean_text(text), text)

    def test_long_runs(self):
        self.assertEqual(pipeline.clean_text("イくっ♡♡♡♡♡♡"), "イくっ♡♡♡")
        self.assertEqual(pipeline.clean_text("やだ★☆♪♫♪ やめて"), "やだ やめて")
        self.assertEqual(pipeline.clean_text("あ" * 30), "あああ")
        self.assertEqual(pipeline.clean_text(""), "")


if __name__ == "__main__":
    unittest.main()
