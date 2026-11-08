"""Draw blocks + translations onto a page for eyeballing quality."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    os.getenv("PREVIEW_FONT", ""),
    # CJK-capable first, so OCR-only previews can show the Japanese source.
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\meiryob.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONT_CANDIDATES:
        if f and Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def _wrap(draw, text, font, width):
    lines, line = [], ""
    # Unspaced (Japanese) text wraps per character; English per word.
    tokens = text.split() if " " in text.strip() else list(text)
    sep = " " if " " in text.strip() else ""
    for word in tokens:
        trial = f"{line}{sep}{word}" if line else word
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw, text, w, h):
    for size in range(28, 9, -1):
        font = _font(size)
        lines = _wrap(draw, text, font, w)
        line_h = size * 1.15
        if len(lines) * line_h <= h and all(draw.textlength(l, font=font) <= w for l in lines):
            return font, lines, line_h
    font = _font(10)
    return font, _wrap(draw, text, font, w), 11.5


def render_preview(img: Image.Image, result: dict, out_path: Path, max_side: int = 2200) -> None:
    scale = min(1.0, max_side / max(img.size))
    page = img.convert("RGB")
    if scale < 1.0:
        page = page.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    overlay = Image.new("RGBA", page.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for sk in result.get("meta", {}).get("skipped", []):
        x1, y1, x2, y2 = (v * scale for v in sk["bbox_px"])
        draw.rectangle((x1, y1, x2, y2), outline=(140, 140, 140, 255), width=2)

    label_font = _font(16)
    for i, b in enumerate(result["blocks"]):
        x1, y1, x2, y2 = (v * scale for v in b["bbox_px"])
        # Text may need more room than the (often narrow, vertical) Japanese box: widen symmetrically.
        w = max(x2 - x1, 150)
        cx = (x1 + x2) / 2
        bx1, bx2 = max(0, cx - w / 2), min(page.width, cx + w / 2)
        draw.rectangle((bx1, y1, bx2, y2), fill=(255, 255, 255, 225))
        draw.rectangle((x1, y1, x2, y2), outline=(230, 40, 40, 255), width=3)
        text = b.get("translation") or "(" + b["source"] + ")"
        font, lines, line_h = _fit(draw, text, bx2 - bx1 - 8, max(y2 - y1 - 6, 40))
        ty = y1 + 4
        for line in lines:
            draw.text((bx1 + 4, ty), line, font=font, fill=(10, 10, 10, 255))
            ty += line_h
        tag = str(i)
        tw = draw.textlength(tag, font=label_font)
        draw.rectangle((x1, y1 - 18, x1 + tw + 8, y1), fill=(230, 40, 40, 255))
        draw.text((x1 + 4, y1 - 18), tag, font=label_font, fill=(255, 255, 255, 255))

    Image.alpha_composite(page.convert("RGBA"), overlay).convert("RGB").save(out_path, optimize=True)
