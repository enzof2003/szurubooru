"""Regression tests for the speaker-colour series (posts tagged translation_error, 2026-11-15):

  1. yellow lettering with a black outline has no near-white pixel next to it: it was invisible to
     the colour mask (25661, 25638, 25637: lines lost, fragmented or not split);
  2. a column at purity 0.79 was "mixed" and its consistent colour discarded (25632);
  3. the detector cut a block of columns in two at one height (25632);
  4. background touching a white bubble passed for coloured lettering, the "groups" were slivers
     that missed the real text (9150);
  5. a lone narrow column read far too short (25694).
Follow-up (translation_errors.md, follow-up 2):
  P2. fixes 1-2 fired on 2D coloured art (25051, 25671, 31430, 7115, 27228, 31931, 31807): those
      blocks must read as before 5b0c572f;
  P3. a split refused for lost ink keeps the block's dominant colour (not the background's);
  P1. a lone column whose read is suspect is read again with its neighbour (_fallback_groups).

Fixture tests need tests/fixtures/ (make_fixtures.py) and are skipped without it; the others are
synthetic. Run like test_columns.py.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import colorsplit  # noqa: E402
import pipeline  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NAMES = ("25661r", "25661l", "25638r", "25637r", "25632l", "9150", "25687", "14207", "14212", "14213")
ART = ("25051", "25671", "31430", "7115a", "7115b", "27228", "31931", "31807", "25547")
needs_fixtures = unittest.skipUnless(all((FIXTURES / f"block_{n}.png").exists() for n in NAMES + ART),
                                     "fixtures missing: run tests/make_fixtures.py <posts dir>")


def block(name) -> Image.Image:
    return Image.open(FIXTURES / f"block_{name}.png").convert("RGB")


def kind(hexc: str | None) -> str:
    """Rough colour name of a group's display colour."""
    if hexc is None:
        return "none"
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    if r > 150 and g > 150 and b < 100:
        return "yellow"
    if g > r and g > b:
        return "green"
    if b > 200 and r < 100:
        return "blue"
    if b > g and r > g:
        return "purple"
    return "other"


def groups_of(name):
    img = block(name)
    return img, colorsplit.split_by_colour(img, [0, 0, img.width, img.height])


def text_lines(g) -> int:
    """Lines of a group that are real columns (not a 」 or ♥ tail)."""
    return sum(1 for b in g.lines if b[3] - b[1] >= 4 * (b[2] - b[0]))


@needs_fixtures
class DarkOutlinedTest(unittest.TestCase):
    """Fix 1: yellow fill / black outline next to white fill / coloured outline."""

    CASES = {  # fixture -> [(colour, text columns)] in reading order
        "25661r": [("green", 1), ("yellow", 2)],
        "25661l": [("green", 1), ("yellow", 2), ("green", 1), ("yellow", 1)],
        "25638r": [("purple", 1), ("yellow", 2), ("purple", 2), ("yellow", 2), ("purple", 1)],
        "25637r": [("yellow", 1), ("purple", 1), ("yellow", 1), ("purple", 2), ("yellow", 1)],
    }

    def test_groups(self):
        for name, want in self.CASES.items():
            with self.subTest(block=name):
                img, groups = groups_of(name)
                self.assertIsNotNone(groups)
                got = [(kind(g.hex), text_lines(g)) for g in groups]
                if name == "25661l":  # the first two green columns touch: one line
                    got[0] = ("green", 1)
                self.assertEqual(got, want)
                xs = [g.box[2] for g in groups]
                self.assertEqual(xs, sorted(xs, reverse=True))  # right to left

    def test_crops_tile_the_block(self):
        # the group crops cover every x of the block: no column can fall between two
        for name in self.CASES:
            with self.subTest(block=name):
                img, groups = groups_of(name)
                edges = sorted((g.crop[0], g.crop[2]) for g in groups)
                self.assertLessEqual(edges[0][0], 0)
                self.assertGreaterEqual(edges[-1][1], img.width)
                for (_, hi), (lo, _) in zip(edges, edges[1:]):
                    self.assertLessEqual(abs(hi - lo), 1)  # cut halfway between the lines

    def test_white_paired_blocks_unchanged(self):
        # no dark-outlined line -> the analysis must not depend on the new mask at all
        for name in ("25687", "14207", "14212", "14213"):
            with self.subTest(block=name):
                img = block(name)
                a = np.asarray(img).astype(np.int16)
                mask, outlined, neutral = colorsplit.lettering_masks(a)
                for vertical in (True, False):
                    with_new = colorsplit._analyse(a, neutral, mask, vertical, 0, 0, None, outlined)
                    legacy = colorsplit._analyse(a, neutral, mask, vertical, 0, 0, None, None)
                    self.assertEqual([(l.s, l.e, l.box, l.n) for l in with_new],
                                     [(l.s, l.e, l.box, l.n) for l in legacy])


class DarkOutlinedSyntheticTest(unittest.TestCase):
    @staticmethod
    def page():
        """Two columns on a brown background: white 'glyphs' with a purple outline, and yellow
        ones with a black outline (hollow squares, strokes 6 px)."""
        img = Image.new("RGB", (160, 600), (120, 85, 60))
        d = ImageDraw.Draw(img)
        for x, fill, outline in ((90, (255, 255, 255), (150, 60, 200)), (20, (240, 205, 0), (0, 0, 0))):
            for y in range(20, 560, 50):
                d.rectangle((x, y, x + 44, y + 44), fill=outline)
                d.rectangle((x + 4, y + 4, x + 40, y + 40), fill=fill)
                d.rectangle((x + 10, y + 10, x + 34, y + 34), fill=outline)
                d.rectangle((x + 14, y + 14, x + 30, y + 30), fill=(120, 85, 60))
        return img

    def test_two_speakers(self):
        img = self.page()
        groups = colorsplit.split_by_colour(img, [0, 0, img.width, img.height])
        self.assertIsNotNone(groups)
        self.assertEqual([kind(g.hex) for g in groups], ["purple", "yellow"])

    def test_vivid_background_is_not_lettering(self):
        # a flat vivid area (no dark outline a stroke apart) adds nothing
        img = Image.new("RGB", (200, 400), (240, 205, 0))
        ImageDraw.Draw(img).rectangle((0, 0, 20, 400), fill=(0, 0, 0))
        _, outlined, _ = colorsplit.lettering_masks(np.asarray(img).astype(np.int16))
        self.assertEqual(int(outlined.sum()), 0)


@needs_fixtures
class AgreedColourTest(unittest.TestCase):
    """Fix 2: 25632, purple / blue / purple x2; the blue column measured purity 0.79."""

    def test_blue_column_is_a_speaker(self):
        img, groups = groups_of("25632l")
        self.assertIsNotNone(groups)
        self.assertEqual([(kind(g.hex), len(g.lines)) for g in groups],
                         [("purple", 1), ("blue", 1), ("purple", 2)])


class JoinCutColumnsTest(unittest.TestCase):
    """Fix 3: two stacked halves + a sub-threshold box holding both + ink through the seam."""

    W, H = 600, 1000

    def ink(self, blank=None):
        ink = np.zeros((self.H, self.W), bool)
        for x in range(110, 400, 48):          # six columns of "glyph" edges, 10..950
            for y in range(10, 950, 6):
                ink[y:y + 5, x:x + 30] = True
        if blank:
            ink[blank[0]:blank[1]] = False
        return ink

    def dets(self, parent=0.249):
        d = [{"box": [100.0, 10.0, 400.0, 484.0], "score": 0.42}, {"box": [95.0, 455.0, 402.0, 955.0], "score": 0.32}]
        if parent:
            d.append({"box": [92.0, 7.0, 404.0, 950.0], "score": parent})
        return d

    def test_joined(self):
        out = pipeline.resolve_text_hierarchy(self.dets(), 0.25, ink=self.ink())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["box"], [95.0, 10.0, 402.0, 955.0])
        self.assertEqual(out[0]["joined"], "cut_columns")

    def test_blank_band_keeps_two_blocks(self):
        out = pipeline.resolve_text_hierarchy(self.dets(), 0.25, ink=self.ink(blank=(460, 480)))
        self.assertEqual(len(out), 2)

    def test_needs_the_enclosing_proposal(self):
        out = pipeline.resolve_text_hierarchy(self.dets(parent=None), 0.25, ink=self.ink())
        self.assertEqual(len(out), 2)

    def test_side_by_side_not_joined(self):
        d = [{"box": [100.0, 10.0, 250.0, 950.0], "score": 0.42}, {"box": [250.0, 10.0, 400.0, 950.0], "score": 0.32},
             {"box": [95.0, 7.0, 404.0, 955.0], "score": 0.2}]
        self.assertEqual(len(pipeline.resolve_text_hierarchy(d, 0.25, ink=self.ink())), 2)

    def test_without_ink_unchanged(self):
        self.assertEqual(len(pipeline.resolve_text_hierarchy(self.dets(), 0.25)), 2)


@needs_fixtures
class LostInkGuardTest(unittest.TestCase):
    """Fix 4: 9150, black text in a white bubble; the colour "groups" were background slivers."""

    def test_split_refused(self):
        img = block("9150")
        groups, info = colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
        self.assertIsNone(groups)
        self.assertEqual(info.get("refused"), "lost_ink")
        self.assertGreater(info["lost_ink"], colorsplit.LOST_INK_MAX)
        notes = pipeline.split_notes_by_colour(img, [{"text_box": [0, 0, img.width, img.height],
                                                      "box": [0, 0, img.width, img.height]}])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["split_refused"], "lost_ink")
        self.assertNotIn("crop", notes[0])

    def test_real_splits_lose_nothing(self):
        for name in ("25687", "25661r", "25661l", "25638r", "25637r", "25632l"):
            with self.subTest(block=name):
                img = block(name)
                groups, info = colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
                self.assertIsNotNone(groups)
                self.assertLess(info["lost_ink"], 0.05)


class ShortOcr:
    """Reads `first` at full size and `half` at half size."""

    def __init__(self, first, half, half_usage=None):
        self.first, self.half, self.half_usage, self.calls = first, half, half_usage or {"completion_tokens": 9}, []

    def __call__(self, crop, glyph=None):
        self.calls.append(crop.size)
        if len(self.calls) == 1:
            return self.first, {"completion_tokens": 7}
        return self.half, dict(self.half_usage)


class ShortReadTest(unittest.TestCase):
    """Fix 5: 25694, one column 46 x 706 px (15 glyph cells) read as 7 characters."""

    COLUMN = [[1141.0, 14.0, 1187.0, 720.0]]
    GOOD = "（でっか…なんなのよコイツら…）"

    def test_short_read(self):
        self.assertTrue(pipeline.short_read("（…ひゃっ）", self.COLUMN))
        self.assertFalse(pipeline.short_read(self.GOOD, self.COLUMN))
        self.assertFalse(pipeline.short_read("「あっ」", [[0.0, 0.0, 46.0, 150.0]]))  # 3 cells: too few to judge
        self.assertFalse(pipeline.short_read("「あっはは！あたいの勝ち～」", [[1286.0, 9.0, 1329.0, 539.0]]))

    def note(self, **kw):
        return {"text_box": [1141.0, 14.0, 1187.0, 720.0], "crop": [1127, 1, 1189, 733], "vertical": True,
                "parts": self.COLUMN, **kw}

    def run_note(self, ocr, note):
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_pad=0.01, ocr_columns_per_call=3, ocr_column_split_min=5)
        pl.ocr = ocr
        pl._ocr_note(Image.new("RGB", (1344, 1008), "white"), note)
        return note

    def test_retry_kept_when_longer(self):
        ocr = ShortOcr("（…ひゃっ）", self.GOOD)
        n = self.run_note(ocr, self.note())
        self.assertEqual(n["source"], self.GOOD)
        self.assertEqual((n["ocr_retry"], n["ocr_short"]), ("0.5x", True))
        self.assertEqual(ocr.calls[1], (ocr.calls[0][0] // 2, ocr.calls[0][1] // 2))

    def test_retry_dropped_when_not_longer_or_looping(self):
        for half, usage in (("（…ひ）", None), ("ぁ" * 60, {"completion_tokens": 512, "truncated": True})):
            ocr = ShortOcr("（…ひゃっ）", half, usage)
            n = self.run_note(ocr, self.note())
            self.assertEqual(n["source"], "（…ひゃっ）")
            self.assertNotIn("ocr_retry", n)
            self.assertTrue(n["ocr_short"])

    def test_good_read_and_unsplit_notes_not_retried(self):
        ocr = ShortOcr(self.GOOD, "x")
        self.run_note(ocr, self.note())
        self.assertEqual(len(ocr.calls), 1)
        ocr = ShortOcr("（…ひゃっ）", self.GOOD)
        n = self.run_note(ocr, {"text_box": [1141.0, 14.0, 1187.0, 720.0], "parts": self.COLUMN})
        self.assertEqual((len(ocr.calls), n["source"]), (1, "（…ひゃっ）"))


@needs_fixtures
class ColouredArtTest(unittest.TestCase):
    """P2: blocks of 2D coloured art read exactly as before 5b0c572f (6f27e1a3)."""

    def analyse(self, name):
        img = block(name)
        groups, info = colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
        return groups, info.get("dominant")

    def test_no_colour(self):
        for name in ("31430", "27228", "31807"):  # black lettering / a title: no colour, no split
            with self.subTest(block=name):
                self.assertEqual(self.analyse(name), (None, None))

    def test_one_colour(self):
        for name, want in (("7115a", "blue"), ("7115b", "red"), ("31931", "red")):
            with self.subTest(block=name):
                groups, dom = self.analyse(name)
                self.assertIsNone(groups)
                self.assertIsNotNone(dom)
                if want == "red":
                    self.assertTrue(dom[0] > 150 and dom[1] < 80 and dom[2] < 80, dom)
                else:
                    self.assertEqual(kind(colorsplit.hex_for(dom)), want)

    def test_split_as_before(self):
        groups, _ = self.analyse("25671")
        self.assertEqual([kind(g.hex) for g in groups], ["green", "purple"])
        groups, _ = self.analyse("25051")
        self.assertEqual(len(groups), 2)  # no slivers: every group is a real column
        self.assertTrue(all(g.box[2] - g.box[0] >= 60 for g in groups))

    def test_no_dark_outlined_line(self):
        for name in ("25051", "25671", "31430", "7115a", "7115b"):
            with self.subTest(block=name):
                a = np.asarray(block(name)).astype(np.int16)
                mask, outlined, neutral = colorsplit.lettering_masks(a)
                bg = colorsplit._background_lab(a, mask | outlined | neutral)
                # columns (these blocks are read as vertical text; rows are checked by the results)
                self.assertEqual(colorsplit._outlined_lines(outlined, mask, True, a, bg), [])


class RefusedSplitColourTest(unittest.TestCase):
    """P3: a block read whole after a refused split keeps its main lettering colour."""

    @staticmethod
    def page(colours):
        """White 'glyphs' with a coloured outline, one column per colour, on a grey background."""
        img = Image.new("RGB", (60 * len(colours) + 20, 600), (110, 110, 110))
        d = ImageDraw.Draw(img)
        for i, c in enumerate(colours):
            x = 10 + 60 * i
            for y in range(20, 560, 50):
                d.rectangle((x, y, x + 44, y + 44), fill=c)
                d.rectangle((x + 5, y + 5, x + 39, y + 39), fill=(255, 255, 255))
                d.rectangle((x + 12, y + 12, x + 32, y + 32), fill=c)
                d.rectangle((x + 16, y + 16, x + 28, y + 28), fill=(255, 255, 255))
        return img

    def analyse(self, img, lost_ink_max):
        old = colorsplit.LOST_INK_MAX
        colorsplit.LOST_INK_MAX = lost_ink_max
        try:
            return colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
        finally:
            colorsplit.LOST_INK_MAX = old

    BLUE, RED = (40, 90, 220), (220, 40, 40)

    def test_main_colour_kept(self):
        img = self.page([self.BLUE] * 4 + [self.RED])
        groups, info = self.analyse(img, 1.0)
        self.assertEqual(len(groups), 2)  # a real split ...
        groups, info = self.analyse(img, -1.0)  # ... refused: read whole, blue (4 of 5 columns)
        self.assertIsNone(groups)
        self.assertEqual(info["refused"], "lost_ink")
        self.assertEqual(kind(colorsplit.hex_for(info["dominant"])), "blue")

    def test_no_main_colour(self):
        groups, info = self.analyse(self.page([self.BLUE, self.RED, self.BLUE, self.RED]), -1.0)
        self.assertIsNone(groups)
        self.assertIsNone(info.get("dominant"))

    @needs_fixtures
    def test_background_is_not_the_colour(self):
        img = block("25547")
        groups, info = colorsplit.analyse_block_info(img, [0, 0, img.width, img.height])
        self.assertIsNone(groups)
        self.assertEqual(info.get("refused"), "lost_ink")
        self.assertIsNone(info.get("dominant"))


class ConfidenceTest(unittest.TestCase):
    def test_letter_tokens_only(self):
        toks = [{"token": "「", "logprob": -3.0}, {"token": "い", "logprob": -0.1}, {"token": "やッ", "logprob": -0.3},
                {"token": "…", "logprob": -2.0}, {"token": "」", "logprob": -1.0}]
        self.assertEqual(pipeline.ocr_confidence(toks), -0.2)
        self.assertIsNone(pipeline.ocr_confidence([{"token": "♥", "logprob": -1.0}]))
        self.assertIsNone(pipeline.ocr_confidence([]))


class ParseTest(unittest.TestCase):
    def test_leaked_json_closers_stripped(self):
        content = '{"translations": [{"id": 0, "text": "Hi"}, {"id": 8, "text": "“I... I don\'t even want something like this...!”}]}}}]}]}"}]}'
        self.assertEqual(pipeline.Translator._parse(content),
                         {0: "Hi", 8: "“I... I don't even want something like this...!”"})

    def test_brackets_kept(self):
        for text in ("Ahh [panting]", "[[sfx]]", "Ehehe♥", "“{weird}” but fine.", ""):
            self.assertEqual(pipeline._strip_json_tail(text), text)


class GarbleCopyTest(unittest.TestCase):
    def test_same_as_server(self):
        # translator/ocr_garble.py is a copy of the server's check (separate images): keep in sync
        here = Path(__file__).resolve().parents[1] / "ocr_garble.py"
        server = Path(__file__).resolve().parents[2] / "server" / "szurubooru" / "func" / "ocr_garble.py"
        if not server.exists():
            self.skipTest("server/ not mounted")
        self.assertEqual(here.read_text(), server.read_text())


class LoneColumnFallbackTest(unittest.TestCase):
    """P1: 25638, the purple lone column 「いやッ…近づけないで…ッ」 (x 957-1004) next to Shou's
    yellow column; alone it read 「ど…ひろな子しば…いやつ」 at low confidence."""

    YELLOW = "「えっへへ…♥鈴仙さんがあまりにもかわいいからこんなになっちゃいました…♥」"
    GOOD = "「いやッ…近づけないで…ッ」"
    BAD = "「ど…ひろな子しば…いやつ」"

    def notes(self, lone_conf=-0.8, lone_text=None):
        yellow = {"box": [1010.0, 10.0, 1100.0, 900.0], "text_box": [1010.0, 10.0, 1100.0, 900.0],
                  "crop": [1006, 0, 1104, 910], "parts": [[1010.0, 10.0, 1050.0, 900.0], [1060.0, 10.0, 1100.0, 900.0]],
                  "vertical": True, "split_from": 0, "color": "#ffde00", "source": self.YELLOW, "ocr_conf": -0.05,
                  "ocr_usage": {}}
        purple = {"box": [957.0, 8.0, 1004.0, 760.0], "text_box": [957.0, 8.0, 1004.0, 760.0],
                  "crop": [950, 0, 1006, 770], "parts": [[957.0, 8.0, 1004.0, 760.0]], "vertical": True,
                  "split_from": 0, "color": "#b75cd6", "source": lone_text or self.BAD, "ocr_conf": lone_conf,
                  "ocr_usage": {}}
        return [yellow, purple]  # reading order: right to left

    def run_fallback(self, notes, merged_text, merged_conf=-0.1):
        pl = object.__new__(pipeline.Pipeline)
        pl.s = SimpleNamespace(ocr_min_conf=-0.5)
        calls = []

        def ocr_note(img, n):
            calls.append(n)
            n.update(source=merged_text, ocr_conf=merged_conf, ocr_usage={})
        pl._ocr_note = ocr_note
        return pl._fallback_groups(Image.new("RGB", (1344, 1008)), notes), calls

    def test_cut_back_when_neighbour_found_exactly(self):
        out, calls = self.run_fallback(self.notes(), self.YELLOW + "\n" + self.GOOD)
        self.assertEqual(len(calls), 1)
        self.assertEqual([(n["color"], n["source"]) for n in out],
                         [("#ffde00", self.YELLOW), ("#b75cd6", self.GOOD)])
        self.assertEqual((out[1]["ocr_fallback"], out[1]["ocr_suspect"]), ("merged_cut", "low_conf"))
        self.assertNotIn("uncertain", out[1])

    def test_plain_merge_otherwise(self):
        # the neighbour's text is not found exactly (one glyph differs): one note, the colour of
        # the group with more lines, right words
        merged = self.YELLOW.replace("鈴仙", "鈴先") + self.GOOD
        out, _ = self.run_fallback(self.notes(), merged)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["color"], out[0]["source"], out[0]["ocr_fallback"]), ("#ffde00", merged, "merged"))
        self.assertEqual(out[0]["parts"], self.notes()[0]["parts"] + self.notes()[1]["parts"])
        self.assertNotIn("uncertain", out[0])

    def test_still_suspect_is_uncertain(self):
        out, _ = self.run_fallback(self.notes(), self.YELLOW + self.BAD, merged_conf=-0.9)
        self.assertEqual(out[1]["source"], self.BAD)
        self.assertTrue(out[1]["uncertain"])

    def test_no_neighbour_is_uncertain(self):
        lone = self.notes()[1]
        out, calls = self.run_fallback([lone], "x")
        self.assertEqual((len(calls), out[0]["source"], out[0]["uncertain"]), (0, self.BAD, True))

    def test_good_or_wide_groups_untouched(self):
        out, calls = self.run_fallback(self.notes(lone_conf=-0.1, lone_text=self.GOOD), "x")
        self.assertEqual(calls, [])
        self.assertEqual([n["source"] for n in out], [self.YELLOW, self.GOOD])
        notes = self.notes()
        notes[0]["ocr_conf"] = -0.9  # low confidence, but a two-column group: not a lone column
        out, calls = self.run_fallback(notes[:1], "x")
        self.assertEqual(calls, [])

    def test_garbled_or_short_lone_column(self):
        for text, reason in (("」をふら～♡も～♡はんきむ「", "garble"), ("（…ひゃっ）", "short")):
            with self.subTest(reason=reason):
                out, _ = self.run_fallback(self.notes(lone_conf=-0.05, lone_text=text), self.YELLOW + self.GOOD)
                self.assertEqual((out[1]["ocr_suspect"], out[1]["source"]), (reason, self.GOOD))


if __name__ == "__main__":
    unittest.main()
