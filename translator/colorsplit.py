"""Split one text block into speaker groups by text colour.

Some pages put several speakers in one block and tell them apart only by colour: coloured
fill with a white outline (red vs green), or white fill with a coloured outline (purple vs red
vs lavender, yellow vs cyan). Steps:

  1. mask    "text-coloured" pixels: tinted, not near-white/near-black, and within a few px of a
             near-white pixel (the fill/outline pair). Background colour rarely touches white, so
             the mask is mostly the lettering itself. Plus vivid fill enclosed by a near-black
             outline (yellow lettering with a black outline, posts 25661/25638/25637): a vivid
             pixel with near-black within ENCLOSE_RADIUS px on both sides, left+right or up+down.
             Used only where it forms a real column of its own (_outlined_lines: long enough,
             not the background colour, not inside a white-paired column); otherwise ignored, so
             2D coloured art reads exactly as without it (25051, 25671, 31430, 7115).
  2. lines   project the lettering (tinted pixels, plus near-black next to near-white for untinted
             text) across the block: vertical text -> columns, horizontal -> rows. Vertical
             columns come from columns() (the OCR chunks' cuts) when it finds more of them.
  3. colour  each line's dominant colour (densest hue window, then its median in CIELAB), or None
             when the line has too little colour (plain black/white text). Mixed lines (a phrase
             highlighted in another colour, e.g. a glowing magenta phrase inside red speech) are
             set aside: only single-colour lines define the speaker colours.
  4. axis    columns (vertical, the Japanese default) unless they are clearly mixed in colour
             (purity < VERTICAL_PURITY) and rows are purer: that is horizontal multi-colour text.
  5. split   only if the line colours form >= 2 clusters at least MIN_DELTA_E apart. A mixed line
             takes the first speaker colour met along it, glyph by glyph (a new speaker starts a
             new line; highlight colours match no speaker and are skipped). Lines are then
             grouped into runs of consecutive same-colour lines in reading order (one run = one
             speaker turn). Untinted lines are never dropped: a real line of text becomes its own
             neutral group (no colour), a short tail (」, ♥, …) joins the previous run. Every line
             found ends up in exactly one group (checked; otherwise no split).
  6. guard   the groups' OCR crops must cover the block's lettering: a split that leaves more
             than LOST_INK_MAX of it outside every crop is refused (the block is then read whole).
             Post 9150: background touching a white bubble passed for coloured lettering and the
             "groups" were slivers that missed the bubble's real (black) text. A block read
             whole this way still gets its main lettering colour when one dominates (and isn't
             the background's).

Plain black-on-white text has no tinted ink at all, so it never splits.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

import numpy as np
from PIL import Image

SAT_MIN = 35          # max(R,G,B) - min(R,G,B) for a pixel to count as coloured (pale lavender ~ 60)
WHITE_MIN = 200       # min(R,G,B) for near-white (fill or outline)
PAIR_RADIUS = 3       # a coloured pixel must be this close to near-white (px)
GAP_FRAC = 0.04       # projection below this fraction of its 90th percentile = gap between lines
MIN_DELTA_E = 30.0    # CIELAB distance between cluster colours for them to be different speakers
MIN_LINE_INK = 0.02   # coloured share of a line's area for its colour to be trusted
MIN_PURITY = 0.5      # share of a line's coloured px in its hue window, below = no colour
MIXED_PURITY = 0.8    # below this the line is mixed (see step 5)
HUE_WIN = 15.0        # half-width (deg) of the dominant hue window
DARK_MAX = 90         # max(R,G,B) for near-black (black text, dark outline of white text)
VERTICAL_PURITY = 0.75  # columns are kept (Japanese default) unless their colour purity is below this
NEUTRAL_MIN_LEN = 2.0   # an untinted line this many line widths long is text of its own (neutral
                        # group); shorter ones (」, ♥, …) attach to the previous group
NEUTRAL = -1
DOMINANT_MIN = 0.6    # unsplit block: share of its text-line length that must be tinted for a colour
EDGE_MARGIN = 0.3     # OCR crop margin past the block's outer edge, in line widths
MAX_LINE_GAP = 1.5    # blank stretch (in line widths) that ends a line's text
VIVID_SAT = 110       # dark-outlined fill: max - min of a vivid pixel ...
VIVID_MIN = 170       # ... and its max(R,G,B)
ENCLOSE_RADIUS = 8    # px: near-black on both sides within this distance (about one stroke width)
CHUNKS_AGREE = 0.8    # a mixed line whose pure glyph chunks agree this much on one colour has it,
CHUNKS_AGREE_MIN = 6  # ... given at least this many pure chunks and a colour DARK_BG_DE from the background
LOST_INK_MAX = 0.15   # a split may leave at most this share of the block's lettering in no crop
# A dark-outlined line must also look like a speaker's column, not 2D-art colour (25051, 25671,
# 31430, 7115 regressed when any vivid patch between dark edges counted as lettering):
DARK_MIN_LEN = 0.2    # its ink runs at least this share of the block's length along the lines
DARK_BG_DE = 30       # its fill differs from the block's background (border, non-lettering) this much
DARK_SHARED_MAX = 0.4  # white-paired ink in its column, as a share of its own fill, stays below this
DISPLAY_S_MIN = 0.6   # returned colour: HSL saturation floor ...
DISPLAY_L = (0.45, 0.60)  # ... and lightness range


@dataclass
class Group:
    box: list[int]             # page px, union of its lines
    rgb: tuple[int, int, int] | None  # measured colour; None = neutral (untinted lettering)
    lines: list[list[int]]     # page px boxes of the member lines, reading order
    label: int                 # colour cluster id on this block; NEUTRAL for untinted lettering
    crop: list[int]            # OCR crop (page px): the block's extent along the lines; across them,
                               # halfway to the neighbouring group's lines, or EDGE_MARGIN line
                               # widths past the block edge (detector boxes can clip the outline)
    vertical: bool             # lines are columns

    @property
    def hex(self) -> str | None:
        """Display colour: the measured one boosted to what the eye sees (see display_rgb)."""
        return "#%02x%02x%02x" % display_rgb(self.rgb) if self.rgb else None

    @property
    def measured_hex(self) -> str | None:
        return "#%02x%02x%02x" % self.rgb if self.rgb else None


def hex_for(rgb: tuple[int, int, int]) -> str:
    """Display hex (#rrggbb) of a measured colour."""
    return "#%02x%02x%02x" % display_rgb(rgb)


def display_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Same hue, HSL saturation >= DISPLAY_S_MIN and lightness clamped to DISPLAY_L: thin
    anti-aliased outlines measure darker/greyer than they look (bright yellow -> olive #929703)."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    s = max(s, DISPLAY_S_MIN)
    l = min(max(l, DISPLAY_L[0]), DISPLAY_L[1])
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary dilation by a (2r+1) square, separable (no scipy in the image)."""
    out = mask.copy()
    for axis in (0, 1):
        src, out = out, out.copy()
        for d in range(1, r + 1):
            if axis == 0:
                out[d:] |= src[:-d]
                out[:-d] |= src[d:]
            else:
                out[:, d:] |= src[:, :-d]
                out[:, :-d] |= src[:, d:]
    return out


def _reach(mask: np.ndarray, axis: int, sign: int, r: int) -> np.ndarray:
    """Pixels with a True pixel of `mask` 1..r px away on one side along `axis`."""
    out = np.zeros_like(mask)
    for k in range(1, r + 1):
        src = [slice(None), slice(None)]
        dst = [slice(None), slice(None)]
        src[axis], dst[axis] = (slice(None, -k), slice(k, None)) if sign > 0 else (slice(k, None), slice(None, -k))
        out[tuple(dst)] |= mask[tuple(src)]
    return out


def _dark_outlined(mx: np.ndarray, mn: np.ndarray) -> np.ndarray:
    """Vivid fill enclosed by a near-black outline: lettering like yellow with a black outline,
    which has no near-white pixel next to it. Background colour is rarely both vivid and squeezed
    between two dark edges a stroke apart."""
    dark = mx < DARK_MAX
    vivid = (mx - mn >= VIVID_SAT) & (mx >= VIVID_MIN)
    r = ENCLOSE_RADIUS
    return vivid & ((_reach(dark, 1, 1, r) & _reach(dark, 1, -1, r)) | (_reach(dark, 0, 1, r) & _reach(dark, 0, -1, r)))


def lettering_masks(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(tinted lettering next to white, vivid lettering in a dark outline, untinted lettering)
    of an int16 RGB array. The first two can overlap: see _analyse."""
    mx, mn = a.max(-1), a.min(-1)
    white = mn >= WHITE_MIN
    near_white = _dilate(white, PAIR_RADIUS)
    mask = (mx - mn >= SAT_MIN) & ~white & (mx >= 60) & near_white        # tinted, next to white
    outlined = _dark_outlined(mx, mn)                                     # vivid, in a dark outline
    neutral = (mx < DARK_MAX) & near_white  # black text, dark outline of white text
    return mask, outlined, neutral


def _hue(a: np.ndarray) -> np.ndarray:
    """Hue (degrees) of an (N,3) RGB array."""
    a = a.astype(float)
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    mx, mn = a.max(1), a.min(1)
    c = np.maximum(mx - mn, 1e-6)
    h = np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4))
    return h * 60.0


def _lab(a: np.ndarray) -> np.ndarray:
    """sRGB (N,3) 0-255 -> CIELAB (D65)."""
    c = a.astype(float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def _runs(profile: np.ndarray, thr: float, min_len: int) -> list[tuple[int, int]]:
    on = profile > thr
    d = np.diff(np.concatenate([[0], on.astype(np.int8), [0]]))
    runs = [(int(a), int(b)) for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if b - a >= min_len]
    merged: list[tuple[int, int]] = []  # tiny gaps: anti-aliasing, dakuten, small kana
    for a, b in runs:
        if merged and a - merged[-1][1] < max(2, min_len // 2):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _profile(mask: np.ndarray, vertical: bool, other: np.ndarray | None = None) -> np.ndarray:
    """Ink of `mask` across the lines; zero where the `other` lettering style has more ink, so the
    lines of the two styles (white-paired, dark-outlined) can never overlap."""
    prof = mask.sum(0 if vertical else 1).astype(float)
    if other is not None:
        prof[other.sum(0 if vertical else 1) > prof] = 0
    return prof


def _lines(mask: np.ndarray, vertical: bool, other: np.ndarray | None = None) -> list[tuple[int, int]]:
    """(start, end) of the text lines: columns (vertical) or rows (horizontal)."""
    prof = _profile(mask, vertical, other)
    hi = np.percentile(prof, 90)
    if hi <= 0:
        return []
    return _runs(prof, GAP_FRAC * hi, max(3, mask.shape[1 if vertical else 0] // 200))


def _colour(px: np.ndarray) -> tuple[np.ndarray, float]:
    """(median Lab of the densest hue window, share of px inside that window)."""
    hues = _hue(px)
    hist = np.bincount((hues // 10).astype(int) % 36, minlength=36)
    k = int(np.argmax(hist + np.roll(hist, 1) + np.roll(hist, -1)))
    d = np.abs(hues - (k + 0.5) * 10) % 360
    near = np.minimum(d, 360 - d) <= HUE_WIN
    return np.median(_lab(px[near]), 0), float(near.mean())


def _rgb(px: np.ndarray, lab: np.ndarray) -> tuple[int, int, int]:
    """Representative RGB: median of the half of px closest to `lab` (drops anti-aliasing)."""
    dist = np.linalg.norm(_lab(px) - lab, axis=1)
    return tuple(int(v) for v in np.median(px[dist <= np.percentile(dist, 50)], 0))


@dataclass
class _Line:
    s: int
    e: int
    box: list[int]               # page px
    lab: np.ndarray | None       # None = uncoloured
    rgb: tuple[int, int, int]
    n: int                       # coloured px
    purity: float
    chunks: list = None          # mixed lines: (Lab, rgb) per glyph-sized chunk, in reading order


def _ink_extent(profile: np.ndarray, width: int) -> tuple[int, int]:
    """(lo, hi) of a line's text along its length: ink runs closer than MAX_LINE_GAP line widths
    are one segment, and the segment with the most ink wins (drops coloured background that
    touches white further along, e.g. a red ribbon next to a white sleeve below the text)."""
    idx = np.where(profile > 0)[0]
    if not len(idx):
        return 0, len(profile)
    cuts = np.where(np.diff(idx) > MAX_LINE_GAP * width)[0]
    segs = np.split(idx, cuts + 1)
    best = max(segs, key=lambda g: profile[g[0]:g[-1] + 1].sum())
    return int(best[0]), int(best[-1]) + 1


def _neutral_runs(neutral: np.ndarray, mask: np.ndarray, runs: list[tuple[int, int]],
                  vertical: bool) -> list[tuple[int, int]]:
    """Untinted lines (black text, white text with a dark outline) in the gaps between the tinted
    lines and the block edges: any gap wide enough to hold a line that has lettering in it gets
    its line(s), so a column without measurable tint can never fall between two groups. (Line
    finding on tinted + dark ink together fails: outlines and panel borders fill the gaps.)"""
    if not runs:
        return []
    width = float(np.median([e - s for s, e in runs]))
    ref = np.percentile(mask.sum(0 if vertical else 1), 90)  # ink per px of a tinted line
    size = mask.shape[1 if vertical else 0]
    bounds = [0] + [v for r in sorted(runs) for v in r] + [size]
    out = []
    for g0, g1 in zip(bounds[0::2], bounds[1::2]):
        if g1 - g0 < 0.6 * width:
            continue
        prof = (neutral[:, g0:g1].sum(0) if vertical else neutral[g0:g1].sum(1)).astype(float)
        out += [(g0 + a, g0 + b) for a, b in _runs(prof, GAP_FRAC * ref, 3) if b - a >= 0.5 * width]
    return out


def _column_lines(mask: np.ndarray, cols: list[tuple[int, int]], other: np.ndarray | None = None) -> list[tuple[int, int]]:
    """Tinted lines from column cuts (crop px, left to right): each column trimmed to its tinted
    ink, so the lines keep their real gaps (for the OCR crops); columns without tinted ink dropped
    (an untinted column is found by _neutral_runs, as with _lines)."""
    prof = _profile(mask, True, other)
    thr = GAP_FRAC * np.percentile(prof, 90)
    out = []
    for lo, hi in cols:
        on = np.where(prof[lo:hi] > thr)[0]
        if len(on):
            out.append((lo + int(on[0]), lo + int(on[-1]) + 1))
    return out


def _background_lab(a: np.ndarray, ink: np.ndarray) -> np.ndarray | None:
    """Lab colour of the block's background: median of the non-lettering pixels on its border."""
    border = np.zeros(ink.shape, bool)
    border[:3] = border[-3:] = True
    border[:, :3] = border[:, -3:] = True
    px = a[border & ~ink]
    return _lab(np.median(px, 0)[None])[0] if len(px) else None


def _outlined_lines(outlined: np.ndarray, mask: np.ndarray, vertical: bool,
                    a: np.ndarray | None = None, bg: np.ndarray | None = None) -> list[tuple[int, int]]:
    """Lines of dark-outlined vivid lettering, found on their own profile: next to white-outlined
    columns there is no gap in a joint profile (the outlines touch; 25661: two green columns and
    a yellow one came out as one line). Only where this style has more ink than the white-paired
    one (see _profile), and only where that ink is as dense as lettering.
    A line must also be a real column of this style (Shou's yellow-on-black lines are 0.45-1.0 of
    the block long, far from the background colour, alone in their column): it runs at least
    DARK_MIN_LEN of the block; its fill is DARK_BG_DE from the background `bg` (Lab; 31430: the
    orange background between black strokes); and its column has little white-paired ink
    (DARK_SHARED_MAX; 25671: a green column whose fill turns red halfway down is one line)."""
    mm = mask & ~_dilate(outlined, PAIR_RADIUS)
    out = []
    for s, e in _lines(outlined, vertical, mask):
        isub = outlined[:, s:e] if vertical else outlined[s:e].T
        lo, hi = _ink_extent(isub.sum(1), e - s)
        fill = int(isub[lo:hi].sum())
        if fill < max(20, MIN_LINE_INK * (hi - lo) * (e - s)):
            continue
        if hi - lo < DARK_MIN_LEN * isub.shape[0]:
            continue
        msub = mm[:, s:e] if vertical else mm[s:e].T
        if msub.sum() >= DARK_SHARED_MAX * fill:
            continue
        if a is not None and bg is not None:
            rgb = a[:, s:e] if vertical else a[s:e].transpose(1, 0, 2)
            fill_lab = _lab(np.median(rgb[isub], 0)[None])[0]
            if np.linalg.norm(fill_lab - bg) < DARK_BG_DE:
                continue
        out.append((s, e))
    return out


def _analyse(a: np.ndarray, neutral: np.ndarray, mask: np.ndarray, vertical: bool, x1: int, y1: int,
             cols: list[tuple[int, int]] | None = None, outlined: np.ndarray | None = None,
             bg: np.ndarray | None = None, dark: list[tuple[int, int]] | None = None) -> list[_Line]:
    """Tinted lines from `mask` and `outlined` (with their colours) plus untinted lines from
    `neutral`. `cols`: column cuts from columns() (crop px), used instead of the fixed-threshold
    line finder for vertical text when they find more columns (it merges columns whose gaps catch
    background tint: post 25687, two speakers' columns read as one mixed column, so no split).
    `dark`: the dark-outlined lines for this axis when the caller already has them (_split)."""
    if dark is None:
        dark = _outlined_lines(outlined, mask, vertical, a, bg) if outlined is not None and outlined.any() else []
    other = outlined if dark else None  # no dark-outlined line: exactly the white-paired analysis
    if dark:
        # Dark-outlined lettering over a white background has its fill within PAIR_RADIUS of
        # white too (thin outline), and its duller anti-aliased edge always is: left in the
        # white-paired mask they bridge the gaps to the neighbouring white-outlined columns (25638).
        mask = mask & ~_dilate(outlined, PAIR_RADIUS)
    tinted = _lines(mask, vertical, other)
    if vertical and cols:
        from_cols = _column_lines(mask, cols, other)
        if len(from_cols) > len(tinted):
            tinted = from_cols
    runs = [(s, e, mask) for s, e in tinted] + [(s, e, outlined) for s, e in dark]
    both = mask | outlined if dark else mask
    spans = sorted((s, e) for s, e, _ in runs)
    runs += [(s, e, neutral) for s, e in _neutral_runs(neutral, both, spans, vertical)]
    out = []
    for s, e, src in sorted(runs, key=lambda r: r[0]):
        isub = src[:, s:e] if vertical else src[s:e].T             # axis 0 runs along the line
        rgb_sub = a[:, s:e] if vertical else a[s:e].transpose(1, 0, 2)
        lo, hi = _ink_extent(isub.sum(1), e - s)
        sub = np.zeros(isub.shape, bool)
        if src is not neutral:
            sub[lo:hi] = isub[lo:hi]
        box = [x1 + s, y1 + lo, x1 + e, y1 + hi] if vertical else [x1 + lo, y1 + s, x1 + hi, y1 + e]
        px = rgb_sub[sub]
        line = _Line(s, e, box, None, (0, 0, 0), len(px), 0.0)
        if len(px) >= max(20, MIN_LINE_INK * (hi - lo) * (e - s)):
            lab, purity = _colour(px)
            line.purity = purity
            if purity >= MIXED_PURITY:
                line.lab, line.rgb = lab, _rgb(px, lab)
            else:
                step = max(4, e - s)  # ~ one glyph
                line.chunks = []
                for c0 in range(lo, hi, step):
                    cp = rgb_sub[c0:c0 + step][sub[c0:c0 + step]]
                    if len(cp) >= 20:
                        cl, cpur = _colour(cp)
                        if cpur >= MIXED_PURITY:
                            line.chunks.append((cl, _rgb(cp, cl)))
                _agreed_colour(line, px, -(-(hi - lo) // step), bg)
        out.append(line)
    return out


def _agreed_colour(line: _Line, px: np.ndarray, steps: int, bg: np.ndarray | None = None) -> None:
    """A "mixed" line whose glyph chunks agree on one colour is a single-colour line after all
    (25632: a blue column at purity 0.79, background tint at its lower end; as a mixed line it
    could only take a colour some pure line had defined, so its speaker was lost). Needs pure
    chunks on at least half of the line, CHUNKS_AGREE of them within MIN_DELTA_E of their median.
    A highlighted phrase inside a line fails this and keeps the glyph-by-glyph rule.
    Short lines (fewer than CHUNKS_AGREE_MIN pure chunks) and a colour close to the block's
    background `bg` (Lab) are left mixed: 27228, 31931 and 31807 had 3-chunk lines "agreeing" on
    the background or a grey, which gave black text a colour or split a sound effect off."""
    if len(line.chunks) < max(CHUNKS_AGREE_MIN, 0.5 * steps):
        return
    labs = np.array([c[0] for c in line.chunks])
    med = np.median(labs, 0)
    near = np.linalg.norm(labs - med, axis=1) < MIN_DELTA_E
    if near.mean() >= CHUNKS_AGREE:
        lab = np.median(labs[near], 0)
        if bg is not None and np.linalg.norm(lab - bg) < DARK_BG_DE:
            return
        line.lab, line.rgb, line.chunks = lab, _rgb(px, lab), None


def _cluster(labs: list[np.ndarray], weights: list[int]) -> list[np.ndarray]:
    """Greedy clustering, heaviest first: a colour joins the first centre within MIN_DELTA_E."""
    centres: list[list] = []  # [lab, weight]
    for i in np.argsort(-np.asarray(weights)):
        for c in centres:
            if np.linalg.norm(c[0] - labs[i]) < MIN_DELTA_E:
                c[0] = (c[0] * c[1] + labs[i] * weights[i]) / (c[1] + weights[i])
                c[1] += weights[i]
                break
        else:
            centres.append([labs[i].copy(), weights[i]])
    return [c[0] for c in centres]


def split_by_colour(img: Image.Image, box: list[float]) -> list[Group] | None:
    """Colour groups of the text block `box` (page px), in reading order.
    None = no split (one colour, uncoloured text, or colours not clearly distinct)."""
    return analyse_block(img, box)[0]


def analyse_block(img: Image.Image, box: list[float]) -> tuple[list[Group] | None, tuple[int, int, int] | None]:
    """(colour groups or None, dominant colour of an unsplit block or None).

    The dominant colour is set only when the block is not split and its lettering is clearly one
    colour: a single colour cluster, and tinted lines make up >= DOMINANT_MIN of the length of all
    its text lines (tinted + untinted). Plain black/white text has none."""
    groups, info = analyse_block_info(img, box)
    return groups, info.get("dominant")


def analyse_block_info(img: Image.Image, box: list[float]) -> tuple[list[Group] | None, dict]:
    """(colour groups or None, info). info: "dominant" (see analyse_block); "lost_ink", the share
    of the block's lettering outside every group crop; "refused" = "lost_ink" when that made the
    split be dropped; "columns", the result of columns(img, box) when the analysis got that far
    (absent otherwise)."""
    info: dict = {}
    return _split(img, box, info), info


def _split(img: Image.Image, box: list[float], info: dict) -> list[Group] | None:
    x1, y1 = int(max(0, box[0])), int(max(0, box[1]))
    x2, y2 = int(min(img.width, box[2])), int(min(img.height, box[3]))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    a = np.asarray(img.crop((x1, y1, x2, y2)).convert("RGB")).astype(np.int16)
    mask, outlined, neutral = lettering_masks(a)
    bg = _background_lab(a, mask | outlined | neutral)
    # Dark-outlined lines per axis, found once here and handed to _analyse (same arguments).
    dark = {v: _outlined_lines(outlined, mask, v, a, bg) for v in (True, False)} if outlined.any() \
        else {True: [], False: []}
    if outlined.any() and not (dark[True] or dark[False]):
        outlined = np.zeros_like(outlined)  # no dark-outlined line: exactly the white-paired analysis
    if (mask | outlined).mean() < 0.005:
        return None
    cols = columns(img, [x1, y1, x2, y2])  # the same column cuts the OCR chunks use
    info["columns"] = cols  # page px, as columns() returns them: reused for the OCR chunks of an unsplit block
    cols = sorted((lo - x1, hi - x1) for lo, hi in cols) if cols else None

    analysed = {}
    for vertical in (True, False):
        lines = _analyse(a, neutral, mask, vertical, x1, y1, cols, outlined, bg, dark[vertical])
        total = sum(l.n for l in lines) or 1
        analysed[vertical] = (sum(l.purity * l.n for l in lines) / total, lines)
    # Vertical (Japanese default) unless its columns are clearly mixed: rows can look "purer" when a
    # column has a highlighted phrase, and cutting vertical text into rows splits sentences.
    pv, lines_v = analysed[True]
    ph, lines_h = analysed[False]
    vertical = bool(lines_v) and (pv >= VERTICAL_PURITY or pv >= ph or len(lines_h) < 2)
    lines = lines_v if vertical else lines_h
    if not lines:
        return None
    width = float(np.median([l.e - l.s for l in lines]))

    def length(l):
        return (l.box[3] - l.box[1]) if vertical else (l.box[2] - l.box[0])

    # Only real lines of text define speaker colours; short ones (」, ♥, a bubble-border fragment)
    # attach to the previous group.
    for l in lines:
        if length(l) < NEUTRAL_MIN_LEN * width:
            l.lab, l.chunks = None, None
    col = [l for l in lines if l.lab is not None]
    if not col:
        return None
    centres = _cluster([l.lab for l in col], [l.n for l in col])
    for l in lines:  # mixed lines take the first speaker colour along them
        for cl, crgb in l.chunks or ():
            if min(np.linalg.norm(c - cl) for c in centres) < MIN_DELTA_E:
                l.lab, l.rgb = cl, crgb
                break
    col = [l for l in lines if l.lab is not None]
    if len(centres) < 2 or len(lines) < 2:
        text_len = sum(length(l) for l in lines if length(l) >= NEUTRAL_MIN_LEN * width)
        if len(centres) == 1 and sum(length(l) for l in col) >= DOMINANT_MIN * max(text_len, 1):
            tot = sum(l.n for l in col)
            info["dominant"] = tuple(int(sum(l.rgb[k] * l.n for l in col) / tot) for k in range(3))
        return None

    # reading order: vertical columns right -> left, horizontal rows top -> bottom
    lines.sort(key=lambda l: -l.s if vertical else l.s)

    def label_of(l):
        if l.lab is not None:
            return min(range(len(centres)), key=lambda c: np.linalg.norm(centres[c] - l.lab))
        # untinted: real text of its own (neutral group), or a short tail (」, ♥, …) -> attach
        return NEUTRAL if length(l) >= NEUTRAL_MIN_LEN * width and (l.e - l.s) >= 0.5 * width else None

    groups: list[dict] = []
    for l in lines:
        lab = label_of(l)
        if groups and (lab is None or groups[-1]["label"] in (lab, None)):
            g = groups[-1]
            if g["label"] is None:  # leading tail line(s) take this run's label
                g["label"] = lab
        else:
            g = {"label": lab, "lines": [], "w": [], "span": []}
            groups.append(g)
        g["lines"].append(l.box)
        g["span"].append((l.s, l.e))
        if lab is not None and lab != NEUTRAL:
            g["w"].append((l.rgb, l.n))
    if len({g["label"] for g in groups} - {NEUTRAL, None}) < 2:
        return None
    # Invariant: every line found goes to exactly one group (nothing is dropped by the split).
    if sum(len(g["lines"]) for g in groups) != len(lines):
        return None
    edges = sorted((l.s, l.e) for l in lines)
    size = (x2 - x1) if vertical else (y2 - y1)
    margin = round(EDGE_MARGIN * width)
    out = []
    for g in groups:
        lo, hi = min(a for a, _ in g["span"]), max(b for _, b in g["span"])
        before = [e for _, e in edges if e <= lo]
        after = [s_ for s_, _ in edges if s_ >= hi]
        c_lo = (lo + max(before)) // 2 if before else -margin
        c_hi = (hi + min(after) + 1) // 2 if after else size + margin
        ls = g["lines"]
        a_lo = min(b[1 if vertical else 0] for b in ls) - margin  # along the lines: the group's text
        a_hi = max(b[3 if vertical else 2] for b in ls) + margin
        crop = [x1 + c_lo, a_lo, x1 + c_hi, a_hi] if vertical else [a_lo, y1 + c_lo, a_hi, y1 + c_hi]
        crop = [max(0, crop[0]), max(0, crop[1]), min(img.width, crop[2]), min(img.height, crop[3])]
        rgb = None
        if g["w"]:
            tot = sum(n for _, n in g["w"]) or 1
            rgb = tuple(int(sum(c[k] * n for c, n in g["w"]) / tot) for k in range(3))
        out.append(Group(box=[min(b[0] for b in ls), min(b[1] for b in ls), max(b[2] for b in ls),
                              max(b[3] for b in ls)], rgb=rgb, lines=ls,
                         label=g["label"] if g["label"] is not None else NEUTRAL, crop=crop,
                         vertical=vertical))
    # Guard: the crops must hold the block's lettering (tinted + untinted), or text is lost.
    ink = mask | outlined | neutral
    covered = np.zeros(ink.shape, bool)
    for g in out:
        covered[max(0, g.crop[1] - y1):max(0, g.crop[3] - y1), max(0, g.crop[0] - x1):max(0, g.crop[2] - x1)] = True
    info["lost_ink"] = round(float((ink & ~covered).sum()) / max(int(ink.sum()), 1), 3)
    if info["lost_ink"] > LOST_INK_MAX:
        info["refused"] = "lost_ink"
        # Read whole, the block still gets its main lettering colour when one clearly dominates
        # (7115: blue and red single-speaker blocks lost their colour to the refusal).
        text_len = sum(length(l) for l in lines if length(l) >= NEUTRAL_MIN_LEN * width)
        heavy = max(range(len(centres)), key=lambda c: sum(length(l) for l in col if label_of(l) == c))
        ls = [l for l in col if label_of(l) == heavy]
        # Not the background, though (25547: white lettering on a night sky).
        if sum(length(l) for l in ls) >= DOMINANT_MIN * max(text_len, 1) and \
                (bg is None or np.linalg.norm(centres[heavy] - bg) >= DARK_BG_DE):
            tot = sum(l.n for l in ls)
            info["dominant"] = tuple(int(sum(l.rgb[k] * l.n for l in ls) / tot) for k in range(3))
        return None
    return out


def speaker_labels(rgbs: list[tuple[int, int, int] | None]) -> list[str]:
    """Page-wide speaker letters for colour groups (same colour across blocks = same letter),
    A, B, C ... in order of first appearance. Neutral groups (None) share one letter."""
    tinted = [c for c in rgbs if c is not None]
    centres = _cluster([_lab(np.array([c]))[0] for c in tinted], [1] * len(tinted)) if tinted else []
    order: list = []
    out = []
    for c in rgbs:
        if c is None:
            k = "neutral"
        else:
            lab = _lab(np.array([c]))[0]
            k = min(range(len(centres)), key=lambda i: np.linalg.norm(centres[i] - lab))
        if k not in order:
            order.append(k)
        out.append(chr(ord("A") + order.index(k)) if order.index(k) < 26 else "?")
    return out


# --------------------------------------------------------------------------- columns (OCR)

COL_SMOOTH = 9            # px, box filter on the column profile
COL_MIN_PITCH = 24        # px, smallest column pitch considered
COL_MIN_AC = 0.2          # autocorrelation the pitch peak must reach (regular columns)
COL_WEAK_AC = 0.1         # weaker peaks at a shorter lag are tried first: columns of alternating
                          # ink strength (post 25687: thin purple outlines between green ones) make
                          # the two-column period the strong peak; kept only if the cuts validate
COL_VALLEY = 0.5          # a gap's profile must be below this share of the lower neighbouring peak
COL_WIDTH = (0.6, 1.5)    # inner column widths, in pitches
COL_MIN_LEN = 3.0         # median column ink length, in pitches (a column holds several glyphs)


def columns(img: Image.Image, box: list[float]) -> list[tuple[int, int]] | None:
    """Vertical text columns of the block `box` as page-px x ranges, right to left, or None when
    the block isn't clearly regular vertical text (then OCR reads it whole).

    Big blocks with many columns are read badly in one OCR call (columns dropped or read out of
    order, repetition loops: posts 14207/14212/14213), so the caller OCRs them a few columns at a
    time. The fixed-threshold line finder (_lines) can't separate these columns: a glowing
    background next to the white outline leaks into the gaps (~15% of the peaks) and columns of
    thin glyphs (もー!…!?) are as faint as that. Instead: the column pitch is the first peak of the
    profile's autocorrelation, and the cuts are its local minima (one per pitch), kept only when
    they are deep valleys and the columns they make are about one pitch wide."""
    x1, y1 = int(max(0, box[0])), int(max(0, box[1]))
    x2, y2 = int(min(img.width, box[2])), int(min(img.height, box[3]))
    w, h = x2 - x1, y2 - y1
    if w < 3 * COL_MIN_PITCH or h < w:  # vertical text blocks of several columns are portrait
        return None
    a = np.asarray(img.crop((x1, y1, x2, y2)).convert("RGB")).astype(np.int16)
    mx, mn = a.max(-1), a.min(-1)
    white = mn >= WHITE_MIN
    near_white = _dilate(white, PAIR_RADIUS)
    mask = (mx - mn >= SAT_MIN) & ~white & (mx >= 60) & near_white   # tinted lettering
    if mask.mean() < 0.005:
        mask = (mx < DARK_MAX) & near_white                          # black text on white
        if mask.mean() < 0.005:
            return None
    prof = np.convolve(mask.sum(0).astype(float), np.ones(COL_SMOOTH) / COL_SMOOTH, mode="same")
    x = prof - prof.mean()
    ac = np.correlate(x, x, "full")[w - 1:]
    if ac[0] <= 0:
        return None
    ac /= ac[0]
    # Pitch candidates: autocorrelation peaks in lag order, up to the first one >= COL_MIN_AC;
    # the first whose cuts validate wins.
    for lag in range(COL_MIN_PITCH, w // 2):
        if not (ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1] and ac[lag] >= COL_WEAK_AC):
            continue
        bounds = _column_bounds(prof, mask, lag)
        if bounds:
            return [(x1 + lo, x1 + hi) for lo, hi in zip(bounds[:-1], bounds[1:])][::-1]
        if ac[lag] >= COL_MIN_AC:
            return None
    return None


def _column_bounds(prof: np.ndarray, mask: np.ndarray, pitch: int) -> list[int] | None:
    """Column cuts (crop px, left to right, 0 and the width included) for a column pitch, or
    None unless they are deep valleys making columns about one pitch wide and several glyphs long."""
    w = len(prof)
    r = int(0.3 * pitch)
    cuts: list[int] = []
    for i in range(r, w - r):
        if prof[i] == prof[i - r:i + r + 1].min() and (not cuts or i - cuts[-1] > r):
            cuts.append(i)
    if not cuts:
        return None
    bounds = [0] + cuts + [w]
    for k, c in enumerate(cuts, 1):  # deep valleys only
        if prof[c] >= COL_VALLEY * min(prof[bounds[k - 1]:c].max(), prof[c:bounds[k + 1]].max()):
            return None
    widths = np.diff(bounds) / pitch
    if any(not COL_WIDTH[0] <= v <= COL_WIDTH[1] for v in widths[1:-1]) or max(widths[0], widths[-1]) > 2.0:
        return None
    lens = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        rows = np.where(mask[:, lo:hi].any(1))[0]
        lens.append(rows[-1] - rows[0] + 1 if len(rows) else 0)
    if np.median(lens) < COL_MIN_LEN * pitch:
        return None
    return bounds
