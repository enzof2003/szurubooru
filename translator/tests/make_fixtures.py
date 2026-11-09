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
}

if __name__ == "__main__":
    posts = Path(sys.argv[1] if len(sys.argv) > 1 else "/var/local/szurubooru/data/posts")
    out = Path(__file__).resolve().parent / "fixtures"
    out.mkdir(exist_ok=True)
    for pid, box in BLOCKS.items():
        src = next(posts.glob(f"{pid}_*"))
        img = Image.open(src).convert("RGB").crop(box)
        img.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
            out / f"block_{pid}.png", optimize=True)
        print(out / f"block_{pid}.png")
