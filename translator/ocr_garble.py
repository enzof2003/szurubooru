"""Deterministic check for garbled OCR text in translator blocks.

The LLM translates every block, including OCR garbage, and then confidently
invents content for it (post 25688: 「♡…は・・・し・・・い、す」 -> "It's... too...
much..."). This check flags such blocks in code, so the note can be marked
uncertain whatever the LLM says. Stdlib only, no szurubooru imports.

Rules (tuned on the 2439 stored blocks of 395 posts, 2026-11-15: 15 flagged,
all genuinely garbled). Each is narrow on purpose:
short SFX and stammering (「す…ご…い」) are normal manga text and must stay
unflagged. Known misses: plausible-looking misreads with no structural sign,
e.g. 「闘れぎ・・・？」 (25677), 「っ…む…ひゃ…やし…そ」 (25689), "い力" (25661).

    reversed-quote  text starts with 」 or ends with 「 (column read upside
                    down / out of order)
    orphan-text     starts inside a quote and has 2+ unquoted runs between
                    quotes (lower halves of several columns merged)
    latin-in-kana   ASCII/full-width letters or digits mixed into a short
                    kana block ("のまOO", "うはNo.7つい"); w/ｗ laughter and
                    censor circles are ignored
    isolated-kana   4+ letter runs, at least 75% of them a single character
                    (「♡…は・・・し・・・い、す」)
    thin-fragment   at most 4 letters and the box is under half as thick as
                    one glyph would be (sliver of a column the colour split
                    cut off); skipped when the text ends in a stretchable
                    mark (ー, …), whose length the glyph count can't predict
"""

import re
import unicodedata
from typing import Any, List, Optional, Sequence

OPEN_QUOTES = "「『（("
CLOSE_QUOTES = "」』）)"
# Marks that join a run of kana (はーっ, ハ～イ) but aren't letters.
LONG_MARKS = "ー〜～"
# Trailing marks that can be drawn arbitrarily long in a column.
STRETCHABLE = "ー〜～…・･‥-"
LAUGH_OR_CENSOR = "wWｗＷ〇○●◯"
THIN_RATIO = 0.5
THIN_MAX_LETTERS = 4
LATIN_MAX_LETTERS = 12


def _char_class(char: str) -> str:
    """H hiragana, K katakana, C kanji, A other letter/digit, S symbol."""
    code = ord(char)
    if 0x3041 <= code <= 0x309F:
        return "H"
    if 0x30A1 <= code <= 0x30FA:
        return "K"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or char in "々〆":
        return "C"
    if char in LONG_MARKS or char in LAUGH_OR_CENSOR:
        return "S"
    if unicodedata.category(char)[0] in "LN":
        return "A"
    return "S"


def _reversed_quote(text: str) -> bool:
    return text[0] in CLOSE_QUOTES or text[-1] in OPEN_QUOTES


def _orphan_text(text: str) -> bool:
    first = next((c for c in text if c in OPEN_QUOTES + CLOSE_QUOTES), "")
    if first not in CLOSE_QUOTES:
        return False
    depth = 0
    closed = False
    run = 0
    orphans = 0
    for char in text:
        if char in OPEN_QUOTES:
            if closed and depth == 0 and run >= 2:
                orphans += 1
            depth += 1
            run = 0
        elif char in CLOSE_QUOTES:
            depth = max(depth - 1, 0)
            closed = True
            run = 0
        elif depth == 0 and closed and _char_class(char) != "S":
            run += 1
    if closed and depth == 0 and run >= 2:
        orphans += 1
    return orphans >= 2


def _latin_in_kana(letters: Sequence[str]) -> bool:
    if len(letters) > LATIN_MAX_LETTERS:
        return False
    classes = [_char_class(c) for c in letters]
    return "A" in classes and ("H" in classes or "K" in classes)


def _isolated_kana(text: str) -> bool:
    joined = "".join(
        c if _char_class(c) != "S" or c in LONG_MARKS else " " for c in text
    )
    runs = joined.split()
    if len(runs) < 4:
        return False
    singles = sum(1 for run in runs if len(run) == 1)
    return singles >= 0.75 * len(runs)


def _thin_fragment(text: str, letters: Sequence[str], bbox: Any) -> bool:
    if len(letters) > THIN_MAX_LETTERS or text[-1] in STRETCHABLE:
        return False
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return False
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return False
    long_side, short_side = max(width, height), min(width, height)
    cells = len(re.sub(r"\s", "", text))
    return short_side < THIN_RATIO * long_side / cells


def garble_reasons(source: Optional[str], bbox_px: Any = None) -> List[str]:
    """Names of the rules this OCR text trips (empty list = looks readable).

    bbox_px is the block's [x0, y0, x1, y1] in image pixels, as the
    translator returns it; without it the thin-fragment rule is skipped.
    """
    text = re.sub(r"\s+", "", source or "")
    letters = [c for c in text if _char_class(c) != "S"]
    if not letters:
        return []
    reasons = []
    if _reversed_quote(text):
        reasons.append("reversed-quote")
    if _orphan_text(text):
        reasons.append("orphan-text")
    if _latin_in_kana(letters):
        reasons.append("latin-in-kana")
    if _isolated_kana(text):
        reasons.append("isolated-kana")
    if bbox_px is not None and _thin_fragment(text, letters, bbox_px):
        reasons.append("thin-fragment")
    return reasons


def is_garbled(source: Optional[str], bbox_px: Any = None) -> bool:
    return bool(garble_reasons(source, bbox_px))
