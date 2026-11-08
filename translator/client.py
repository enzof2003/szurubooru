"""CLI client for the translate service.

  python client.py page.jpg                      -> prints the JSON response
  python client.py --save translate-test\\*       -> writes <name>.json + <name>_preview.png next to each image
  python client.py --save --no-translate ...     -> detection + OCR only
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import config
import requests

from pipeline import load_image
from render import render_preview

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def translate_file(url: str, key: str, path: Path, translate: bool = True) -> dict:
    with path.open("rb") as f:
        r = requests.post(
            f"{url}/translate",
            params={"translate": str(translate).lower()},
            headers={"X-API-Key": key},
            files={"image": (path.name, f)},
            timeout=900,
        )
    if r.status_code != 200:
        raise RuntimeError(f"{path.name}: HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def main() -> int:
    s = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--url", default=f"http://{s.host}:{s.port}")
    ap.add_argument("--save", action="store_true", help="write <name>.json and <name>_preview.png")
    ap.add_argument("--no-translate", action="store_true")
    ap.add_argument("--summary", help="write a JSON summary of all runs to this file")
    args = ap.parse_args()

    paths = []
    for pattern in args.images:
        for p in sorted(glob.glob(pattern)) or [pattern]:
            p = Path(p)
            if p.suffix.lower() in IMAGE_EXT and not p.stem.endswith("_preview"):
                paths.append(p)

    summary = []
    for p in paths:
        try:
            res = translate_file(args.url.rstrip("/"), s.api_key, p, not args.no_translate)
        except Exception as e:
            print(f"ERROR {e}", file=sys.stderr)
            summary.append({"image": p.name, "error": str(e)})
            continue
        if not args.save:
            sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(res, ensure_ascii=False, indent=2))
            continue
        try:
            p.with_suffix(".json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            preview = p.with_name(p.stem + "_preview.png")
            tmp = preview.with_name(preview.stem + ".tmp.png")
            render_preview(load_image(p.read_bytes()), res, tmp)
            os.replace(tmp, preview)
        except OSError as e:  # e.g. preview open in an image viewer
            print(f"WARN {p.name}: could not write outputs: {e}", file=sys.stderr)
        m = res["meta"]
        usage = (m.get("llm") or {}).get("usage") or {}
        row = {"image": p.name, "size": f"{res['width']}x{res['height']}", "blocks": len(res["blocks"]),
               "skipped": len(m["skipped"]), **m["timings_ms"],
               "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
               "missing_ids": (m.get("llm") or {}).get("missing_ids")}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
