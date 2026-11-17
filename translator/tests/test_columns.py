"""Regression tests: tall vertical blocks read in column chunks, OCR/note text cleaning.

Fixtures are the text blocks of posts 14207, 14212, 14213, 25687 (256-colour PNGs; the column cuts
move by <= 2 px against the originals). Whole, the first three were OCRed with columns missing or
out of order and, on 14207, a ♦♩♦♩… loop up to max_tokens. 25687 has two speakers told apart by
colour (purple outlines in columns 1 and 4, green elsewhere): its columns were merged, so it gave
one green note. They are crops of the booru's images, so they
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
BLOCKS = {14207: 7, 14212: 8, 14213: 6, 25687: 6}


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


def is_purple(hexc: str) -> bool:
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return r > g and b > g


@needs_fixtures
class ColourSplitColumnsTest(unittest.TestCase):
    """25687: purple text in columns 1 and 4, green in 2-3 and 5-6 -> four speaker turns."""

    def test_weak_pitch_peak(self):
        # alternating ink strength: the column pitch is a weak autocorrelation peak (0.18), the
        # two-column period the strong one; the column cuts must still be found
        img = block(25687)
        cols = colorsplit.columns(img, [0, 0, img.width, img.height])
        self.assertEqual(len(cols), 6)
        widths = [hi - lo for lo, hi in cols]
        self.assertLess(max(widths) / min(widths), 1.3)

    def test_split_per_speaker_right_to_left(self):
        img = block(25687)
        groups = colorsplit.split_by_colour(img, [0, 0, img.width, img.height])
        self.assertIsNotNone(groups)
        self.assertEqual([is_purple(g.hex) for g in groups], [True, False, True, False])
        self.assertEqual([len(g.lines) for g in groups], [1, 2, 1, 2])
        self.assertTrue(all(g.vertical for g in groups))
        xs = [g.box[0] for g in groups]
        self.assertEqual(xs, sorted(xs, reverse=True))
        # the lines are the OCR column cuts, trimmed: each line inside exactly one column
        cols = colorsplit.columns(img, [0, 0, img.width, img.height])
        lines = [l for g in groups for l in g.lines]
        for (lo, hi), l in zip(cols, lines):
            self.assertTrue(lo <= l[0] < l[2] <= hi, (lo, hi, l))

    def test_notes(self):
        img = block(25687)
        notes = pipeline.split_notes_by_colour(img, [{"text_box": [0, 0, img.width, img.height],
                                                      "box": [0, 0, img.width, img.height]}])
        self.assertEqual(len(notes), 4)
        self.assertEqual([n["speaker"] for n in notes], ["A", "B", "A", "B"])


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

    def test_colour_group_chunked_on_its_columns(self):
        # a colour group with enough columns is chunked too, along colorsplit's lines (the same
        # column cuts); split_min lowered so 25687's two-column green groups qualify
        img = block(25687)
        notes = pipeline.split_notes_by_colour(img, [{"text_box": [0, 0, img.width, img.height],
                                                      "box": [0, 0, img.width, img.height]}])
        cols = colorsplit.columns(img, [0, 0, img.width, img.height])
        orig_crop = img.crop

        def crop(box):
            c = orig_crop(box)
            c.info["x"] = (box[0], box[2])
            return c

        img.crop = crop
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=1, ocr_column_split_min=2)
        seen = []
        for n in notes:
            pl.ocr = FakeOcr(cols)
            pl._ocr_note(img, n)
            seen.append(n["source"].replace("\n", ""))
            self.assertEqual(n.get("ocr_chunks"), [2, 2] if len(n["parts"]) == 2 else None)
            # chunk crops stay inside the group's crop: never the neighbouring speaker's column
            for (lo, hi), _ in pl.ocr.calls:
                self.assertGreaterEqual(lo, round(n["crop"][0]))
                self.assertLessEqual(hi, round(n["crop"][2]))
        # every column once, right to left, across the four notes
        self.assertEqual("".join(seen), "".join(f"[{i}]" for i in range(6)))

    def test_disabled(self):
        _, calls, note = self.run_note(14213, per_call=0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("ocr_chunks", note)


READ = "あいうえお" * 6


class LoopOcr:
    """Loops (hits max_tokens) on crops narrower than `loop_below` px, except at half size when
    `half_ok`; answers with the crop's page x range otherwise."""

    def __init__(self, loop_below, half_ok):
        self.loop_below, self.half_ok, self.calls = loop_below, half_ok, []

    def __call__(self, crop, glyph=None):
        lo, hi = crop.info["x"]  # kept by resize(): a half-size crop is narrower than its range
        half = crop.width < 0.75 * (hi - lo)
        self.calls.append("half" if half else crop.info["x"])
        if (half and self.half_ok) or (not half and crop.width >= self.loop_below):
            # a read of plausible length (a lone column read short is a suspect read: P1)
            return READ + ("" if half else str(crop.info["x"])), {"completion_tokens": 5}
        return "ぁ" * 50, {"completion_tokens": 512, "truncated": True}


@needs_fixtures
class LoopFallbackTest(unittest.TestCase):
    """A colour group read on its own loops: retry at half size, else merge with a neighbour."""

    def notes(self):
        img = block(25687)
        notes = pipeline.split_notes_by_colour(img, [{"text_box": [0, 0, img.width, img.height],
                                                      "box": [0, 0, img.width, img.height]}])
        orig_crop = img.crop

        def crop(box):
            c = orig_crop(box)
            c.info["x"] = (box[0], box[2])
            return c

        img.crop = crop
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=3, ocr_column_split_min=5, ocr_min_conf=-99)
        return img, notes, pl

    def run_all(self, pl, img, notes):
        for n in notes:
            pl._ocr_note(img, n)
        return pl._fallback_groups(img, notes)

    def test_retry_half_size(self):
        img, notes, pl = self.notes()
        pl.ocr = LoopOcr(loop_below=60, half_ok=True)  # single columns (~45-50 px) loop
        out = self.run_all(pl, img, notes)
        self.assertEqual(len(out), 4)
        singles = [n for n in out if len(n["parts"]) == 1]
        self.assertTrue(singles and all(n["ocr_retry"] == "0.5x" and n["source"] == READ for n in singles))
        self.assertTrue(all(not n["ocr_usage"].get("truncated") for n in out))

    def test_loop_that_stops_by_itself(self):
        # 25687: 「あぁぁぁぁぁぁぁぁぁ♡♦♦♦… then EOS at 195 tokens, under max_tokens
        raw = "「あぁぁぁぁぁぁぁぁぁ♡" + "♦" * 55
        self.assertTrue(pipeline.looped(raw, {"completion_tokens": 195}))
        self.assertTrue(pipeline.looped("「あ", {"completion_tokens": 512, "truncated": True}))
        for ok in ("「うそ…妖夢にあんな大きいのが…っ」", "イくっ♡♡♡♡♡♡", "あああああああああっ♡♡♡♡"):
            self.assertFalse(pipeline.looped(ok, {"completion_tokens": 20}), ok)

    def test_merge_when_retry_loops(self):
        img, notes, pl = self.notes()
        pl.ocr = LoopOcr(loop_below=60, half_ok=False)
        out = self.run_all(pl, img, notes)
        # purple(1) + green(2) and purple(1) + green(2): each lone column merged with the next group
        self.assertEqual(len(out), 2)
        self.assertTrue(all(n["merged_on_loop"] for n in out))
        self.assertEqual([len(n["parts"]) for n in out], [3, 3])
        self.assertFalse(any(n.get("ocr_looped") for n in out))
        self.assertFalse(any(n.get("uncertain") for n in out))  # the pairs read fine
        xs = [n["text_box"][0] for n in out]
        self.assertEqual(xs, sorted(xs, reverse=True))  # reading order kept
        self.assertEqual([is_purple(n["color"]) for n in out], [False, False])  # more lines: green

    def test_merge_that_loops_too_is_uncertain(self):
        img, notes, pl = self.notes()
        pl.ocr = LoopOcr(loop_below=10 ** 6, half_ok=False)  # every read loops, the pairs' too
        out = self.run_all(pl, img, notes)
        merged = [n for n in out if n.get("merged_on_loop")]
        self.assertTrue(merged)
        self.assertTrue(all(n.get("ocr_looped") and n.get("uncertain") for n in merged))

    def test_unsplit_note_never_retried(self):
        img = block(14213)
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=0, ocr_column_split_min=5)
        pl.ocr = LoopOcr(loop_below=10 ** 6, half_ok=True)
        note = {"text_box": [0, 0, img.width, img.height]}
        orig_crop = img.crop

        def crop(box):
            c = orig_crop(box)
            c.info["x"] = (box[0], box[2])
            return c

        img.crop = crop
        pl._ocr_note(img, note)
        self.assertEqual(len(pl.ocr.calls), 1)
        self.assertNotIn("ocr_retry", note)


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


class PrepareTest(unittest.TestCase):
    """PaddleOcr._prepare: crops resized for the OCR model (synthetic black-square 'glyphs')."""

    @staticmethod
    def page(glyph: int, n: int = 6) -> Image.Image:
        from PIL import ImageDraw, ImageFilter
        step = round(glyph * 1.4)
        img = Image.new("RGB", (step * n + glyph, step * n + glyph), "white")
        d = ImageDraw.Draw(img)
        for i in range(n):
            for j in range(n):
                x, y = glyph // 2 + i * step, glyph // 2 + j * step
                d.rectangle((x, y, x + glyph - 1, y + glyph - 1), "black")
        return img.filter(ImageFilter.GaussianBlur(0.7))  # 2 grey levels only: Otsu finds no ink

    @staticmethod
    def ocr(**kw) -> pipeline.PaddleOcr:
        s = dict(ocr_upscale_below=16, ocr_upscale=3, ocr_downscale_pitch_above=56,
                 ocr_downscale_pitch_to=48, ocr_max_pixels=300_000)
        return pipeline.PaddleOcr(SimpleNamespace(**{**s, **kw}), None)

    def test_large_pitch_downscaled(self):
        ocr, crop = self.ocr(ocr_max_pixels=0), self.page(100)
        out = ocr._prepare(crop, pitch=140)
        self.assertAlmostEqual(out.width, crop.width * 48 / 140, delta=1)
        self.assertAlmostEqual(ocr.last_scale, 48 / 140, delta=0.002)

    def test_normal_pitch_untouched(self):
        for pitch in (20, 40, 56):
            with self.subTest(pitch=pitch):
                ocr, crop = self.ocr(), self.page(30, n=4)
                self.assertEqual(ocr._prepare(crop, pitch=pitch).size, crop.size)
                self.assertIsNone(ocr.last_scale)

    def test_max_pixels_large_glyphs(self):
        ocr, crop = self.ocr(), self.page(100)  # 940 x 940
        w, h = ocr._prepare(crop).size
        self.assertLessEqual(w * h, 300_000 * 1.01)
        self.assertEqual(w, h)
        # no glyph estimate at all (blank): capped too
        w, h = self.ocr()._prepare(Image.new("RGB", (3000, 2000), "white")).size
        self.assertLessEqual(w * h, 300_000 * 1.01)
        self.assertAlmostEqual(w / h, 1.5, delta=0.01)

    def test_max_pixels_spares_normal_glyphs(self):
        ocr, crop = self.ocr(), self.page(30, n=20)  # 870 x 870 of 30 px glyphs: over the cap
        self.assertGreater(crop.width * crop.height, 300_000)
        self.assertEqual(ocr._prepare(crop).size, crop.size)
        self.assertIsNone(ocr.last_scale)

    def test_small_glyphs_still_upscaled(self):
        ocr, crop = self.ocr(), self.page(10)
        self.assertEqual(ocr._prepare(crop).size, (crop.width * 3, crop.height * 3))
        self.assertEqual(ocr.last_scale, 3)

    def test_disabled(self):
        ocr, crop = self.ocr(ocr_downscale_pitch_above=0, ocr_max_pixels=0), self.page(100)
        self.assertEqual(ocr._prepare(crop, pitch=140).size, crop.size)


if __name__ == "__main__":
    unittest.main()


@needs_fixtures
class ReuseTest(unittest.TestCase):
    """The colour analysis hands its column cuts and outline lines on instead of the pipeline
    finding them a second time; the results must be the ones a fresh call gives."""

    def test_info_columns_are_what_columns_returns(self):
        for pid in (14213, 25687):
            img = block(pid)
            box = [0, 0, img.width, img.height]
            _, info = colorsplit.analyse_block_info(img, box)
            self.assertIn("columns", info)
            self.assertEqual(info["columns"], colorsplit.columns(img, box), pid)
            # a float box (as the detector gives) lands on the same pixels
            fbox = [0.4, 0.7, img.width - 0.2, img.height - 0.6]
            _, info = colorsplit.analyse_block_info(img, fbox)
            self.assertEqual(info["columns"], colorsplit.columns(img, fbox), pid)

    def test_no_columns_entry_when_the_analysis_stops_early(self):
        img = Image.new("RGB", (6, 6), "white")
        _, info = colorsplit.analyse_block_info(img, [0, 0, 6, 6])  # too small
        self.assertNotIn("columns", info)
        img = Image.new("RGB", (200, 400), "white")
        _, info = colorsplit.analyse_block_info(img, [0, 0, 200, 400])  # no lettering at all
        self.assertNotIn("columns", info)

    def test_unsplit_note_carries_the_columns_and_ocr_does_not_look_again(self):
        img = block(14213)
        box = [0.0, 0.0, float(img.width), float(img.height)]
        notes = pipeline.split_notes_by_colour(img, [{"box": box, "text_box": box, "parts": [box], "kind": "text"}])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["columns"], colorsplit.columns(img, box))
        fresh = dict(notes[0])
        del fresh["columns"]
        orig_crop = img.crop

        def crop(box):
            c = orig_crop(box)
            c.info["x"] = (box[0], box[2])
            return c

        img.crop = crop

        def read(note, patched):
            pl = object.__new__(pipeline.Pipeline)
            pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=3, ocr_column_split_min=5)
            pl.ocr = FakeOcr(notes[0]["columns"])
            if patched:
                orig = colorsplit.columns
                colorsplit.columns = lambda *a, **k: self.fail("columns() called again")
                try:
                    pl._ocr_note(img, note)
                finally:
                    colorsplit.columns = orig
            else:
                pl._ocr_note(img, note)
            return note["source"], note.get("ocr_chunks"), pl.ocr.calls

        self.assertEqual(read(dict(notes[0]), True), read(fresh, False))

    def test_outline_lines_found_once_per_axis(self):
        img = block(25687)
        calls = []
        orig = colorsplit._outlined_lines

        def counting(outlined, mask, vertical, *a, **k):
            calls.append(vertical)
            return orig(outlined, mask, vertical, *a, **k)

        colorsplit._outlined_lines = counting
        try:
            colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
        finally:
            colorsplit._outlined_lines = orig
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(len(calls), len(set(calls)))
