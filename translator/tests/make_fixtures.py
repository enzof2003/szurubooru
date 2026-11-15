"""Recreate tests/fixtures/ (not in git: crops of the booru's images) from the post images.

  docker run --rm -v "$PWD/translator:/work" -v /var/local/szurubooru/data/posts:/posts:ro \
      -w /work --entrypoint python szuru-translate tests/make_fixtures.py /posts
"""

import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None
# post -> text block bbox (page px, from the detector)
BLOCKS = {
    14207: [3070, 37, 3916, 1332],
    14212: [2928, 33, 3918, 1316],
    14213: [3192, 30, 3916, 1325],
    25687: [8, 9, 292, 1003],  # two speakers by colour: purple columns 1 and 4, green 2-3 and 5-6
    # test_speakers.py (names block_<key>.png)
    "25661r": (25661, [1186, 16, 1331, 1000]),  # green/white outline, then 2 columns yellow/black outline
    "25661l": (25661, [8, 11, 338, 993]),       # green x2, yellow x2, green, yellow
    "25638r": (25638, [958, 9, 1329, 920]),     # purple, yellow x2, purple x2, yellow x2, purple
    "25637r": (25637, [1052, 10, 1334, 997]),   # yellow, purple, yellow, purple x2, yellow
    "25632l": (25632, [4, 6, 200, 984]),        # purple, blue (purity 0.79), purple x2
    "9150": (9150, [50, 812, 256, 1668]),       # black text in a white bubble + background colour
    # regressions of 5b0c572f on 2D coloured art (must read as before it, 6f27e1a3)
    "25051": (25051, [2099, 224, 2471, 901]),   # black text, red + orange outline (2 groups)
    "25671": (25671, [5, 17, 245, 1002]),       # green-outlined column turning red, purple x3
    "31430": (31430, [16, 1530, 309, 2102]),    # black SFX on an orange background (no colour)
    "7115a": (7115, [278, 575, 536, 1348]),     # one blue speaker
    "7115b": (7115, [89, 572, 278, 1251]),      # one red speaker
    "27228": (27228, [2537, 779, 2616, 1492]),  # black text, white outline, cream background
    "31931": (31931, [1633, 82, 2293, 1229]),   # red dialogue + grey SFX (one red block)
    "31807": (31807, [968, 430, 1210, 1349]),   # title (no colour before)
    "25547": (25547, [405, 40, 500, 454]),      # white text, grey outline, night sky: refused, no colour
}

if __name__ == "__main__":
    posts = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/local/szurubooru/data/posts")
    out = Path(__file__).resolve().parent / "fixtures"
    out.mkdir(exist_ok=True)
    for name, box in BLOCKS.items():
        pid, box = (name, box) if isinstance(name, int) else box
        src = next(posts.glob(f"{pid}_*"))
        img = Image.open(src).convert("RGB").crop(box)
        if isinstance(name, str) and name[:1] in "237":  # 2D-art regressions: lossless (quantizing
            img.save(out / f"block_{name}.png", optimize=True)  # to 256 colours changes their result)
        else:
            img.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
                out / f"block_{name}.png", optimize=True)
        print(out / f"block_{name}.png")
