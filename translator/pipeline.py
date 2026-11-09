"""Manga page -> notes (box, Japanese OCR, translation).

Stages:
  1. detect   Koharu Layout RF-DETR Seg 2XL (ONNX Runtime, CPU): text / onomatopoeia / bubble / panel
  2. group    text boxes inside the same bubble -> one note; free text -> one note per box;
              classes in SKIP_CLASSES (default: onomatopoeia) are only counted in meta.skipped,
              except onomatopoeia >= 90% inside a bubble, which is dialogue and is handled as text
  3. order    manga reading order: panels right->left, top->bottom (XY-cut), then notes inside each panel
  3b. colour  SPLIT_BY_COLOR: a block whose columns/lines carry clearly different text colours
              (several speakers in one block) becomes one note per colour run (colorsplit.py)
  4. ocr      PaddleOCR-VL 1.6 (llama-server) on each note's text region, one call per note
  5. filter   drop notes with no Japanese (Latin text, digits, bare punctuation)
  6. llm      one OpenAI-compatible chat call per page, JSON in / JSON out
"""

from __future__ import annotations

import base64
import atexit
import io
import json
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import childproc
import colorsplit
import config

import numpy as np
import onnxruntime as ort
import requests
from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = 80_000_000  # ~9000x9000; refuse decompression bombs beyond that

# A block is worth translating only if it has an actual kana/kanji LETTER. Marks that are
# punctuation on their own (・ ー ゛ ゜ ヽ ゝ 々, halfwidth ･ ｰ ﾞ ﾟ) must not count, or a wordless
# "・・・" / "ーー" bubble would be sent to the LLM (and retried once when it comes back empty).
JAPANESE_RE = re.compile(
    "[ぁ-ゖ"   # hiragana letters (no ゛゜ゝゞ)
    "ァ-ヺ"    # katakana letters incl. ヵヶ (no ・ー ヽヾ)
    "ㇰ-ㇿ"    # small katakana extension
    "ｦ-ｯｱ-ﾝ"  # halfwidth katakana letters (no ･ ｰ ﾞ ﾟ)
    "㐀-䶿一-鿿"  # kanji
    "〆]"          # 〆
)
CLASSES = ("text", "onomatopoeia", "bubble", "panel")  # RF-DETR class ids 0..3


# --------------------------------------------------------------------------- helpers


def load_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def has_japanese(text: str) -> bool:
    return bool(JAPANESE_RE.search(text))


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _iou(a, b) -> float:
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


def _union(boxes) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def xy_cut(boxes: list[list[float]], shrink: float = 0.1) -> list[int]:
    """Manga reading order by recursive XY-cut.

    Split into horizontal bands where no box crosses (read top -> bottom); if no horizontal gap,
    split into vertical strips (read right -> left); recurse. Boxes are shrunk a little so slight
    overlaps do not block a cut. Falls back to (top, right-to-left) when nothing can be cut.
    """
    def shrunk(i):
        x1, y1, x2, y2 = boxes[i]
        dx, dy = (x2 - x1) * shrink / 2, (y2 - y1) * shrink / 2
        return x1 + dx, y1 + dy, x2 - dx, y2 - dy

    def gaps(idx, lo, hi):
        spans = sorted((shrunk(i)[lo], shrunk(i)[hi], i) for i in idx)
        groups, cur, end = [], [spans[0][2]], spans[0][1]
        for s, e, i in spans[1:]:
            if s > end:
                groups.append(cur)
                cur, end = [i], e
            else:
                cur.append(i)
                end = max(end, e)
        groups.append(cur)
        return groups

    def rec(idx):
        if len(idx) <= 1:
            return list(idx)
        rows = gaps(idx, 1, 3)  # cut along y -> bands, top to bottom
        if len(rows) > 1:
            return [i for r in rows for i in rec(r)]
        cols = gaps(idx, 0, 2)  # cut along x -> strips, left to right ...
        if len(cols) > 1:
            return [i for c in reversed(cols) for i in rec(c)]  # ... read right to left
        return sorted(idx, key=lambda i: (boxes[i][1], -boxes[i][2]))

    return rec(list(range(len(boxes))))


# --------------------------------------------------------------------------- detection


class RfDetr:
    """Koharu Layout RF-DETR Seg 2XL exported to ONNX (see tools/export_rfdetr_onnx.py)."""

    MEAN = np.array([0.485, 0.456, 0.406], np.float32)
    STD = np.array([0.229, 0.224, 0.225], np.float32)
    NUM_SELECT = 160

    def __init__(self, path: Path, threads: int, thresholds: dict[str, float]):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # The arena keeps the ~6 GB activation peak of the 2XL model resident forever; without it
        # memory is returned after each page (~3 GB transient, ~0.25 GB idle) at no speed cost.
        opts.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.res = self.session.get_inputs()[0].shape[-1]
        self.outputs = [o.name for o in self.session.get_outputs()]
        self.thresholds = thresholds

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        # Antialiased bilinear resize in float, per channel (matches torchvision's F.resize on tensors).
        arr = np.asarray(img, np.float32) / 255.0
        chans = [np.asarray(Image.fromarray(np.ascontiguousarray(arr[..., c])).resize((self.res, self.res), Image.BILINEAR))
                 for c in range(3)]
        x = (np.stack(chans, -1) - self.MEAN) / self.STD
        return x.transpose(2, 0, 1)[None].astype(np.float32)

    def __call__(self, img: Image.Image) -> list[dict]:
        # No pre-downscale: resizing twice shifts scores by up to 0.8 and saves only ~90 MB.
        W, H = img.size
        named = dict(zip(self.outputs, self.session.run(None, {self.input_name: self._preprocess(img)})))
        boxes, logits = named["dets"][0], named["labels"][0]  # (Q,4) cxcywh normalized, (Q,C+1)
        prob = 1.0 / (1.0 + np.exp(-logits))
        flat = prob.reshape(-1)
        top = np.argpartition(-flat, self.NUM_SELECT)[: self.NUM_SELECT]
        dets = []
        for k in top[np.argsort(-flat[top])]:
            q, c = divmod(int(k), prob.shape[1])
            if c >= len(CLASSES):
                continue
            name, score = CLASSES[c], float(flat[k])
            # text: keep weak sub-boxes down to 0.1; resolve_text_hierarchy applies the real threshold
            floor = min(self.thresholds.get(name, 1.01), 0.1) if name == "text" else self.thresholds.get(name, 1.01)
            if score < floor:
                continue
            cx, cy, w, h = boxes[q]
            box = [max(0.0, (cx - w / 2) * W), max(0.0, (cy - h / 2) * H),
                   min(float(W), (cx + w / 2) * W), min(float(H), (cy + h / 2) * H)]
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            dets.append({"cls": name, "score": score, "box": box})
        return self._dedup(dets)

    @staticmethod
    def _dedup(dets: list[dict]) -> list[dict]:
        """Same query/region reported twice: keep the higher score (text vs onomatopoeia compete)."""
        kept: list[dict] = []
        for d in sorted(dets, key=lambda d: -d["score"]):
            rivals = {"text", "onomatopoeia"} if d["cls"] in ("text", "onomatopoeia") else {d["cls"]}
            if all(not (k["cls"] in rivals and _iou(d["box"], k["box"]) > 0.6) for k in kept):
                kept.append(d)
        return kept


# --------------------------------------------------------------------------- OCR


class OcrServer:
    """Starts PaddleOCR-VL in llama-server (CPU by default) and stops it with the service."""

    def __init__(self, s: config.Settings):
        self.s = s
        self.proc: subprocess.Popen | None = None
        self.api_key = secrets.token_urlsafe(24)  # only this process can talk to its OCR server
        self.base_url = f"http://127.0.0.1:{s.ocr_port}/v1"

    def start(self) -> None:
        s = self.s
        for p in (s.ocr_bin, s.ocr_model, s.ocr_mmproj):
            if not p.exists():
                raise FileNotFoundError(p)
        cmd = [str(s.ocr_bin), "-m", str(s.ocr_model), "--mmproj", str(s.ocr_mmproj),
               "--host", "127.0.0.1", "--port", str(s.ocr_port), "--api-key", self.api_key,
               "-t", str(s.threads), "-tb", str(s.threads), "-c", "4096", "-np", "1",
               "-ngl", str(s.ocr_gpu_layers), "--no-webui", "--jinja"]
        if s.ocr_gpu_layers == 0:
            cmd.append("--no-mmproj-offload")
        health = f"http://127.0.0.1:{s.ocr_port}/health"
        try:
            requests.get(health, timeout=2)
            stale = True
        except requests.ConnectionError:
            stale = False
        except requests.RequestException:
            stale = True
        if stale:  # its health check would pass and our api key would be refused
            raise RuntimeError(f"port {s.ocr_port} is already in use (left-over OCR llama-server?)")
        log = (config.ROOT / "logs").joinpath("ocr_server.log")
        log.parent.mkdir(exist_ok=True)
        self._log = log.open("wb")
        # Tied to this process: the OS kills it if the service dies without running stop().
        self.proc = childproc.spawn_tied(cmd, stdout=self._log, stderr=subprocess.STDOUT)
        atexit.register(self.stop)
        childproc.install_exit_on_signals()
        deadline = time.time() + 180
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"OCR server exited with code {self.proc.returncode}; see {log}")
            try:
                if requests.get(health, timeout=2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError("OCR server did not become ready in 180 s")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        if getattr(self, "_log", None):
            self._log.close()
            self._log = None


def _runs(mask: np.ndarray, min_len: int = 2) -> list[tuple[int, int]]:
    """(start, end) of the True runs of a 1-D mask, at least min_len long."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return [(int(a), int(b)) for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if b - a >= min_len]


def _otsu(gray: np.ndarray) -> int:
    p = np.bincount(gray.reshape(-1), minlength=256).astype(float)
    p /= p.sum()
    w, mu = np.cumsum(p), np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * w - mu) ** 2 / (w * (1 - w))
    return int(np.nanargmax(between))


def glyph_size(crop: Image.Image) -> float | None:
    """Rough glyph size (px) of dark text: median width of the ink columns and height of the ink lines.

    Projected on the axis across the lines, each column (vertical text) or line (horizontal) is one
    run ~ one glyph wide; projected along them, several lines merge into one long run, so when the
    two medians disagree by > 2x the smaller one is the glyph. None = no usable dark ink (light text
    on a dark or busy background, where the estimate would be meaningless)."""
    gray = np.asarray(crop.convert("L"))
    if gray.max() - gray.min() < 32:  # blank / flat crop (Otsu is undefined on one grey level)
        return None
    ink = gray < min(_otsu(gray), 160)
    if ink.mean() < 0.005:
        return None
    meds = []
    for axis in (0, 1):
        r = [b - a for a, b in _runs(ink.any(axis))]
        if not r:
            return None
        r = [x for x in r if x >= 0.3 * max(r)]  # dots, furigana, noise
        meds.append(float(np.median(r)))
    lo, hi = sorted(meds)
    return lo if hi > 2 * lo else hi


class PaddleOcr:
    """PaddleOCR-VL 1.6 'OCR:' task, same decoding settings as Koharu (greedy, repeat penalty 1.2)."""

    def __init__(self, s: config.Settings, server: OcrServer | None):
        self.s, self.server = s, server
        self.session = requests.Session()
        self.last_upscale: float | None = None  # factor applied to the last crop (for meta)

    def _url_key(self):
        if self.server:
            if not self.server.alive():
                self.server.start()  # crashed: restart once per request
            return self.server.base_url, self.server.api_key
        return self.s.ocr_base_url, self.s.ocr_api_key

    def _prepare(self, crop: Image.Image) -> Image.Image:
        w, h = crop.size
        g = glyph_size(crop) if self.s.ocr_upscale_below > 0 and self.s.ocr_upscale > 1 else None
        self.last_upscale = None
        if g is not None and g < self.s.ocr_upscale_below:
            # Tested on post 3180 (~9 px glyphs, AVIF): plain upscaling changed nothing, a blur
            # after the upscale smooths compression noise / screentone, the unsharp mask restores edges.
            f = self.last_upscale = self.s.ocr_upscale
            crop = crop.resize((round(w * f), round(h * f)), Image.LANCZOS)
            crop = crop.filter(ImageFilter.GaussianBlur(f / 3)).filter(ImageFilter.UnsharpMask(1.5, 80, 2))
            w, h = crop.size
        if self.s.ocr_max_pixels and w * h > self.s.ocr_max_pixels:
            f = (self.s.ocr_max_pixels / (w * h)) ** 0.5
            crop = crop.resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS)
        if min(crop.size) < 32:  # tiny crops: give the vision encoder at least ~one patch row
            f = 32 / min(crop.size)
            crop = crop.resize((round(crop.width * f), round(crop.height * f)), Image.LANCZOS)
        return crop

    def __call__(self, crop: Image.Image) -> tuple[str, dict]:
        url, key = self._url_key()
        buf = io.BytesIO()
        self._prepare(crop).save(buf, format="PNG")
        body = {
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": "OCR:"},
            ]}],
            "temperature": 0, "max_tokens": 512, "repeat_penalty": 1.2, "repeat_last_n": 512,
        }
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        r = self.session.post(f"{url}/chat/completions", json=body, headers=headers, timeout=self.s.ocr_timeout)
        r.raise_for_status()
        data = r.json()
        text = (data["choices"][0]["message"].get("content") or "").replace("️", "").strip()
        return text, data.get("usage") or {}


# --------------------------------------------------------------------------- notes


def _coverage(parent, parts) -> float:
    """Fraction of `parent` covered by the union of `parts` (coarse raster, 64x64 cells)."""
    x1, y1, x2, y2 = parent
    w, h = max(x2 - x1, 1e-6), max(y2 - y1, 1e-6)
    grid = np.zeros((64, 64), bool)
    for p in parts:
        a = np.clip([(p[0] - x1) / w, (p[2] - x1) / w], 0, 1) * 64
        b = np.clip([(p[1] - y1) / h, (p[3] - y1) / h], 0, 1) * 64
        grid[int(b[0]):int(np.ceil(b[1])), int(a[0]):int(np.ceil(a[1]))] = True
    return float(grid.mean())


LOST_INK_MAX = 0.15  # a split may leave at most this share of the parent's ink uncovered


def ink_map(img: Image.Image) -> np.ndarray:
    """Strong-edge pixels (grey step > 60 to the right/below neighbour): a cheap proxy for lettering."""
    g = np.asarray(img.convert("L")).astype(np.int16)
    e = np.zeros(g.shape, bool)
    e[:, 1:] |= np.abs(np.diff(g, axis=1)) > 60
    e[1:, :] |= np.abs(np.diff(g, axis=0)) > 60
    return e


def _lost_ink(ink: np.ndarray, parent, kept) -> float:
    """Share of the ink inside `parent` that none of the `kept` boxes covers."""
    x1, y1 = max(0, int(parent[0])), max(0, int(parent[1]))
    x2, y2 = min(ink.shape[1], int(np.ceil(parent[2]))), min(ink.shape[0], int(np.ceil(parent[3])))
    sub = ink[y1:y2, x1:x2]
    total = int(sub.sum())
    if not total:
        return 0.0
    cov = np.zeros(sub.shape, bool)
    for b in kept:
        cov[max(0, int(b[1]) - y1):max(0, int(np.ceil(b[3])) - y1),
            max(0, int(b[0]) - x1):max(0, int(np.ceil(b[2])) - x1)] = True
    return float((sub & ~cov).sum()) / total


def resolve_text_hierarchy(texts: list[dict], threshold: float, floor: float = 0.1,
                           ink: np.ndarray | None = None, accounted: list | None = None) -> list[dict]:
    """RF-DETR often proposes a whole text block *and* its columns/sub-blocks.

    Keep whichever level the model is more confident about: a node is split into its children only
    if a child scores higher than the node and the children cover >= 50% of it (children may be
    below the threshold, down to `floor`). Chosen boxes that overlap are then merged.

    With `ink` (see ink_map), a split must also not leave more than LOST_INK_MAX of the node's ink
    outside the children and the `accounted` boxes (e.g. onomatopoeia): on 25698 the children of a
    3-column block covered 66% of its area but missed the top of one column (30% of its ink).
    """
    cand = [t for t in texts if t["score"] >= floor]

    def inside(child, parent):
        return (child is not parent and _area(parent["box"]) > 1.1 * _area(child["box"])
                and _inter(child["box"], parent["box"]) / max(_area(child["box"]), 1.0) >= 0.8)

    parent_of = {}
    for c in cand:
        containers = [p for p in cand if inside(c, p)]
        if containers:
            parent_of[id(c)] = min(containers, key=lambda p: _area(p["box"]))
    children: dict[int, list[dict]] = {}
    for c in cand:
        if id(c) in parent_of:
            children.setdefault(id(parent_of[id(c)]), []).append(c)

    def ancestors(c):
        while id(c) in parent_of:
            c = parent_of[id(c)]
            yield c

    def choose(node) -> list[dict]:
        kids = children.get(id(node), [])
        if kids and max(k["score"] for k in kids) > node["score"]:
            chosen = [x for k in kids for x in choose(k)]
            boxes = [x["box"] for x in chosen]
            if _coverage(node["box"], boxes) >= 0.5 and (
                    ink is None or _lost_ink(ink, node["box"], boxes + (accounted or [])) <= LOST_INK_MAX):
                return chosen
        return [node]

    roots = [t for t in cand if t["score"] >= threshold and all(a["score"] < threshold for a in ancestors(t))]
    return _merge_overlapping([dict(x) for r in roots for x in choose(r)])


def _merge_overlapping(chosen: list[dict]) -> list[dict]:
    """Overlapping pieces of the same block -> one box (in place on the given dicts)."""
    merged = True
    while merged:
        merged = False
        for i in range(len(chosen)):
            for j in range(i + 1, len(chosen)):
                a, b = chosen[i], chosen[j]
                if _inter(a["box"], b["box"]) > 0.2 * min(_area(a["box"]), _area(b["box"])):
                    a["box"], a["score"] = _union([a["box"], b["box"]]), max(a["score"], b["score"])
                    if b.get("origin") and not a.get("origin"):
                        a["origin"] = b["origin"]
                    del chosen[j]
                    merged = True
                    break
            if merged:
                break
    return chosen


BUBBLE_INSIDE = 0.6  # share of a box's area inside a bubble for it to belong to that bubble
# Onomatopoeia must be almost fully inside: breath marks drawn on bubble edges (ふ～～) stay skipped.
ONOMATOPOEIA_IN_BUBBLE = 0.9


def build_notes(dets: list[dict], skip: tuple[str, ...], W: int, H: int,
                text_threshold: float = 0.25, ink: np.ndarray | None = None) -> tuple[list[dict], list[dict]]:
    """Group text boxes by bubble and sort in reading order. Returns (notes, skipped)."""
    bubbles = [d for d in dets if d["cls"] == "bubble"]
    panels = [d for d in dets if d["cls"] == "panel"]

    def in_bubble(d) -> bool:
        return any(_inter(d["box"], b["box"]) / max(_area(d["box"]), 1.0) >= ONOMATOPOEIA_IN_BUBBLE
                   for b in bubbles)

    # Onomatopoeia inside a bubble is dialogue in stylized lettering (e.g. チンポでかっ): treat as text.
    promoted = [{**d, "cls": "text", "origin": "onomatopoeia_in_bubble"}
                for d in dets if d["cls"] == "onomatopoeia" and in_bubble(d)]
    texts = resolve_text_hierarchy([d for d in dets if d["cls"] == "text"], text_threshold, ink=ink,
                                   accounted=[d["box"] for d in dets if d["cls"] == "onomatopoeia"])
    texts = _merge_overlapping(texts + promoted)
    skipped = [{**d, "reason": f"class:{d['cls']}"} for d in dets
               if d["cls"] in skip and not (d["cls"] == "onomatopoeia" and in_bubble(d))]
    if "text" in skip:
        texts = []

    # 1. bubble grouping: a text box belongs to the bubble that contains most of it
    groups: dict[int, list[dict]] = {}
    free: list[dict] = []
    for t in texts:
        best, best_c = None, 0.0
        for bi, b in enumerate(bubbles):
            c = _inter(t["box"], b["box"]) / max(_area(t["box"]), 1.0)
            if c > best_c:
                best, best_c = bi, c
        if best is not None and best_c >= BUBBLE_INSIDE:
            groups.setdefault(best, []).append(t)
        else:
            free.append(t)

    notes = []
    for bi, members in groups.items():
        order = xy_cut([m["box"] for m in members])
        members = [members[i] for i in order]
        notes.append({"box": bubbles[bi]["box"], "text_box": _union([m["box"] for m in members]),
                      "parts": [m["box"] for m in members], "kind": "bubble",
                      "score": max(m["score"] for m in members),
                      **({"origin": "onomatopoeia_in_bubble"} if any(m.get("origin") for m in members) else {})})
    for t in free:
        notes.append({"box": t["box"], "text_box": t["box"], "parts": [t["box"]], "kind": "text", "score": t["score"]})

    # 2. reading order: panels first (right->left, top->bottom), then notes inside each panel
    panel_boxes = [p["box"] for p in panels] or [[0.0, 0.0, float(W), float(H)]]
    porder = xy_cut(panel_boxes, shrink=0.02)
    rank = {pi: r for r, pi in enumerate(porder)}

    def panel_of(n):
        tb = n["text_box"]
        cx, cy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
        inside = [pi for pi, p in enumerate(panel_boxes) if p[0] <= cx <= p[2] and p[1] <= cy <= p[3]]
        if inside:  # nested panels: the smallest one wins
            return min(inside, key=lambda pi: _area(panel_boxes[pi]))
        return max(range(len(panel_boxes)), key=lambda pi: _inter(tb, panel_boxes[pi]))

    by_panel: dict[int, list[dict]] = {}
    for n in notes:
        by_panel.setdefault(panel_of(n), []).append(n)
    ordered = []
    for pi in sorted(by_panel, key=lambda p: rank[p]):
        ns = by_panel[pi]
        ordered.extend(ns[i] for i in xy_cut([n["text_box"] for n in ns]))
    return ordered, skipped


def split_notes_by_colour(img: Image.Image, notes: list[dict]) -> list[dict]:
    """Replace each note whose text block mixes clearly different text colours by one note per
    colour run (reading order kept). Split notes get `color` (#rrggbb, display-boosted; none for
    a neutral group), `speaker` (page-wide letter: same colour = same letter), `crop` (OCR region)
    and `split_from`. An unsplit block with one clear lettering colour gets `color` and `speaker`."""
    out = []
    for i, n in enumerate(notes):
        groups, dominant = colorsplit.analyse_block(img, n["text_box"])
        if not groups:
            if dominant:  # one clear lettering colour: the note still gets it (and a speaker letter)
                n = {**n, "rgb": dominant, "color": colorsplit.hex_for(dominant),
                     "measured_color": "#%02x%02x%02x" % dominant}
            out.append(n)
            continue
        for g in groups:
            out.append({**n, "box": [float(v) for v in g.box], "text_box": [float(v) for v in g.box],
                        "parts": [[float(v) for v in b] for b in g.lines], "crop": g.crop,
                        "vertical": g.vertical, "rgb": g.rgb, "split_from": i,
                        **({"color": g.hex, "measured_color": g.measured_hex} if g.rgb else {})})
    split = [n for n in out if "split_from" in n or "color" in n]
    for n, letter in zip(split, colorsplit.speaker_labels([n["rgb"] for n in split])):
        n["speaker"] = letter
    return out


# --------------------------------------------------------------------------- LLM


def read_glossary(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        jp, en = line.split("=", 1)
        if jp.strip() and en.strip():
            entries.append((jp.strip(), en.strip()))
    return entries


# Post tags sent by the caller as image context: {"character": [...], "series": [...], "artist": [...]}.
CONTEXT_ROLES = ("character", "series", "artist")
CONTEXT_MAX_TAGS = 30
CONTEXT_MAX_LEN = 100


def clean_context_tags(raw) -> dict[str, list[str]]:
    """Validate/normalise the request's context_tags. Unknown roles and non-string tags are dropped;
    raises ValueError when it isn't an object of lists."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("context_tags must be an object like {\"character\": [\"tag\", ...]}")
    out: dict[str, list[str]] = {}
    for role in CONTEXT_ROLES:
        tags = raw.get(role) or []
        if not isinstance(tags, list):
            raise ValueError(f"context_tags.{role} must be a list of strings")
        clean = []
        for t in tags:
            if isinstance(t, str) and t.strip() and len(t) <= CONTEXT_MAX_LEN and t.strip() not in clean:
                clean.append(t.strip())
        if clean:
            out[role] = clean[:CONTEXT_MAX_TAGS]
    return out


class LlmError(RuntimeError):
    pass


class Translator:
    def __init__(self, s: config.Settings):
        self.s = s
        self.session = requests.Session()

    def _page_glossary(self, sources: list[str]) -> dict[str, str]:
        joined = "\n".join(sources)
        return {jp: en for jp, en in read_glossary(self.s.glossary_file) if jp in joined}

    def _character_notes(self) -> dict[str, str]:
        return {tag.lower(): note for tag, note in read_glossary(self.s.characters_file)}

    def _context(self, context_tags: dict[str, list[str]] | None) -> str:
        """Image context from the post's tags; "" when there are none (prompt then unchanged)."""
        if not context_tags:
            return ""

        def show(tags: list[str]) -> str:
            return ", ".join(t.replace("_", " ") for t in tags)

        chars, series, artists = (context_tags.get(r) or [] for r in CONTEXT_ROLES)
        parts = []
        if chars:
            parts.append("characters: " + show(chars))
        if series:
            parts.append("series: " + show(series))
        lines = []
        if parts:
            lines.append("Characters/series in this image: " + "; ".join(parts))
        if artists:
            lines.append("Artist: " + show(artists))
        known = self._character_notes()
        notes = [f"- {known[t.lower()]}" for t in chars if t.lower() in known]
        if notes:
            lines.append(
                "Character notes (who they are; use them to recognise puns and lines that are literal "
                "for that character, but don't force them into lines they don't fit):\n" + "\n".join(notes)
            )
        return "\n".join(lines)

    def _instructions(self) -> str:
        f = self.s.instructions_file
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""

    def _system_prompt(self, sources: list[str], speakers: bool = False, context: str = "") -> str:
        s = self.s
        instructions = self._instructions()
        glossary = list(self._page_glossary(sources).items())
        parts = [
            f"You are a professional manga translator. Translate Japanese manga text into natural {s.target_language}.",
            "The user sends JSON: {\"segments\": [{\"id\": int, \"text\": str, \"box\": [x1, y1, x2, y2]}]}. "
            "Each segment is one speech bubble or one free-standing text block, in manga reading order "
            "(right-to-left, top-to-bottom); line breaks inside a segment are column/line breaks of the same text. "
            "box is the position on the page on a 0-1000 scale. OCR may contain mistakes: "
            "fix obvious ones from context" + (" and the attached page image." if s.vision else "."),
            "Reply with ONLY a JSON object, no markdown, no commentary: "
            "{\"translations\": [{\"id\": int, \"text\": str}]} with exactly one entry per input id.",
        ]
        if speakers:
            parts.append(
                "Some segments have a \"speaker\" letter: on this page characters are told apart by the "
                "colour of their lettering (sometimes several share one text block). The same letter is "
                "the same character everywhere on the page, a different letter is a different character "
                "answering. Translate each segment as that character's own words (keep who says what), "
                "and do not put the letter in the translation."
            )
        if instructions:
            parts.append(
                "Translator instructions (the input JSON repeats them as \"instructions\", right before the "
                "segments; they are binding for every segment):\n" + instructions
            )
        if context:
            parts.append(
                "Image context from the post's tags (the input JSON repeats it as \"context\"): the tagged "
                "characters are usually the ones speaking or being talked about. Use their names, natures and "
                "powers to understand the jokes.\n" + context
            )
        if glossary:
            parts.append(
                "Glossary: the input JSON may contain a \"glossary\" object mapping Japanese terms to their "
                "required English rendering. Whenever a segment contains one of these terms, the translation "
                "MUST use that English rendering verbatim (never a romanization of your own).\n"
                + "\n".join(f"{jp} = {en}" for jp, en in glossary)
            )
        return "\n\n".join(parts)

    def _image_part(self, img: Image.Image) -> dict:
        scale = min(1.0, self.s.vision_max_side / max(img.size))
        small = img if scale >= 1.0 else img.resize(
            (round(img.width * scale), round(img.height * scale)), Image.LANCZOS
        )
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}

    @staticmethod
    def _parse(content: str) -> dict[int, str]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise
            data = json.loads(text[start : end + 1])
        items = data.get("translations", data) if isinstance(data, dict) else data
        out = {}
        for item in items:
            try:
                if item["text"] is not None:  # null would otherwise become the string "None"
                    out[int(item["id"])] = str(item["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def translate(self, img: Image.Image, blocks: list[dict],
                  context_tags: dict[str, list[str]] | None = None) -> tuple[list[str | None], dict]:
        """One call for the page; segments the LLM dropped or left empty get one retry on their own.
        Still-missing segments come back as None (listed in missing_ids), never as ""."""
        ids = list(range(len(blocks)))
        context = self._context(context_tags)
        got, meta = self._call(img, blocks, ids, context)
        meta["retried_ids"] = [i for i in ids if i not in got]
        if meta["retried_ids"]:
            try:
                more, m2 = self._call(img, blocks, meta["retried_ids"], context)
                got.update(more)
                for k, v in m2["usage"].items():
                    meta["usage"][k] = meta["usage"].get(k, 0) + v
                meta["attempts"] += m2["attempts"]
            except LlmError as e:  # keep what the first call returned
                meta["retry_error"] = str(e)
        meta["missing_ids"] = [i for i in ids if i not in got]
        if context:
            meta["context"] = context
        return [got.get(i) for i in ids], meta

    def _call(self, img: Image.Image, blocks: list[dict], ids: list[int],
              context: str = "") -> tuple[dict[int, str], dict]:
        """Translate blocks[ids] (ids kept as segment ids). Returns ({id: non-empty text}, meta)."""
        s = self.s
        W, H = img.size
        sources = [blocks[i]["source"] for i in ids]
        segments = [
            {
                "id": i,
                "text": blocks[i]["source"],
                "box": [round(blocks[i]["box"][0] / W * 1000), round(blocks[i]["box"][1] / H * 1000),
                        round(blocks[i]["box"][2] / W * 1000), round(blocks[i]["box"][3] / H * 1000)],
                **({"speaker": blocks[i]["speaker"]} if blocks[i].get("speaker") else {}),
            }
            for i in ids
        ]
        payload: dict = {}
        instructions = self._instructions()
        if instructions:
            # Repeated right before the segments, like the glossary: Gemma ignored them when they
            # were only in the system prompt.
            payload["instructions"] = instructions
        if context:
            payload["context"] = context
        payload["segments"] = segments
        glossary = self._page_glossary(sources)
        if glossary:
            # Repeated next to the segments: Gemma ignored it when it was only in the system prompt.
            payload["glossary"] = glossary
        user_text = json.dumps(payload, ensure_ascii=False)
        user_content: list[dict] | str = user_text
        if s.vision:
            user_content = [{"type": "text", "text": user_text}, self._image_part(img)]

        body: dict = {
            "model": s.llm_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(
                    sources, speakers=any(blocks[i].get("speaker") for i in ids), context=context)},
                {"role": "user", "content": user_content},
            ],
            "temperature": s.llm_temperature,
            "max_tokens": s.llm_max_tokens,
            "stream": False,
        }
        if not s.reasoning:
            body["reasoning"] = {"enabled": False}                       # OpenRouter
            body["chat_template_kwargs"] = {"enable_thinking": False}    # llama-server / vLLM / LM Studio
        if s.llm_json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(s.llm_extra_body)

        headers = {"Content-Type": "application/json"}
        if s.llm_api_key:
            headers["Authorization"] = f"Bearer {s.llm_api_key}"

        last_err: Exception | None = None
        usage_total: dict = {}
        for attempt in range(2):
            try:
                r = self.session.post(
                    f"{s.llm_base_url}/chat/completions", json=body, headers=headers, timeout=s.llm_timeout
                )
                if r.status_code == 400 and "response_format" in body:
                    body.pop("response_format")  # some providers reject json mode: retry without it
                    continue
                if r.status_code >= 400:
                    raise LlmError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
                data = r.json()
                for k, v in (data.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        usage_total[k] = usage_total.get(k, 0) + v
                content = data["choices"][0]["message"].get("content") or ""
                parsed = self._parse(content)
                got = {i: parsed[i] for i in ids if parsed.get(i)}  # unknown ids and empty strings dropped
                return got, {"usage": usage_total, "model": data.get("model", s.llm_model),
                             "attempts": attempt + 1}
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError) as e:
                last_err = e  # malformed reply: retry once
            except requests.RequestException as e:
                raise LlmError(f"LLM request failed: {e}") from e
        raise LlmError(f"LLM returned unparseable output: {last_err}")


# --------------------------------------------------------------------------- pipeline


@dataclass
class Pipeline:
    s: config.Settings
    detector: RfDetr = field(init=False)
    ocr_server: OcrServer | None = field(init=False, default=None)
    ocr: PaddleOcr = field(init=False)
    translator: Translator = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self):
        if self.s.ocr_server == "managed":
            self.ocr_server = OcrServer(self.s)
            self.ocr_server.start()
        self.detector = RfDetr(self.s.det_model, self.s.threads, self.s.det_thresholds)
        self.ocr = PaddleOcr(self.s, self.ocr_server)
        self.translator = Translator(self.s)

    def close(self) -> None:
        if self.ocr_server:
            self.ocr_server.stop()

    def warmup(self) -> dict:
        """One tiny detection + OCR call so the first real page isn't slow.

        Detection always runs at the model's fixed input size, and one OCR call goes through the
        whole vision encoder + language model, so every weight page is read in (the GGUF is
        mmapped: on a cold page cache the first page's OCR is ~10x slower otherwise)."""
        t: dict = {}
        img = Image.new("RGB", (256, 256), "white")
        with self._lock:
            t0 = time.perf_counter()
            self.detector(img)
            t["detect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            t0 = time.perf_counter()
            self.ocr(img.crop((0, 0, 64, 32)))
            t["ocr_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return t

    def detect_and_ocr(self, img: Image.Image) -> tuple[list[dict], list[dict], dict]:
        """Returns (japanese_notes, skipped, timings_ms). Serialized: one page at a time."""
        t: dict = {}
        with self._lock:
            t0 = time.perf_counter()
            dets = self.detector(img)
            notes, skipped = build_notes(dets, self.s.skip_classes, *img.size,
                                         text_threshold=self.s.det_thresholds.get("text", 0.25),
                                         ink=ink_map(img))
            t["detect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if self.s.split_by_color:
                t0, c0 = time.perf_counter(), time.process_time()
                notes = split_notes_by_colour(img, notes)
                t["color_split_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                t["color_split_cpu_ms"] = round((time.process_time() - c0) * 1000, 1)

            t0 = time.perf_counter()
            per_note = []
            for n in notes:
                t1 = time.perf_counter()
                x1, y1, x2, y2 = n.get("crop") or n["text_box"]
                d = self.s.ocr_pad * max(x2 - x1, y2 - y1)
                # colour groups: no margin across the lines, it would reach the neighbouring speaker
                dx = dy = d
                if n.get("crop"):
                    dx, dy = (0, d) if n["vertical"] else (d, 0)
                crop = img.crop((round(max(0, x1 - dx)), round(max(0, y1 - dy)),
                                 round(min(img.width, x2 + dx)), round(min(img.height, y2 + dy))))
                n["source"], n["ocr_usage"] = self.ocr(crop)
                if self.ocr.last_upscale:
                    n["upscale"] = self.ocr.last_upscale
                per_note.append(round((time.perf_counter() - t1) * 1000, 1))
            t["ocr_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            t["ocr_ms_per_note"] = per_note

        kept = []
        for n in notes:
            if has_japanese(n["source"]):
                kept.append(n)
            else:
                skipped.append({**n, "cls": "text", "reason": "no_japanese"})
        return kept, skipped, t

    def run(self, img: Image.Image, translate: bool = True,
            context_tags: dict[str, list[str]] | None = None) -> dict:
        t_start = time.perf_counter()
        W, H = img.size
        notes, skipped, timings = self.detect_and_ocr(img)
        llm_meta: dict = {}
        translations: list[str | None] = [None] * len(notes)
        if translate and notes:
            t0 = time.perf_counter()
            translations, llm_meta = self.translator.translate(img, notes, context_tags)
            timings["llm_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        timings["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)

        def geom(box):
            x1, y1, x2, y2 = (round(v) for v in box)
            return {"polygon": [[round(x1 / W, 5), round(y1 / H, 5)], [round(x2 / W, 5), round(y1 / H, 5)],
                                [round(x2 / W, 5), round(y2 / H, 5)], [round(x1 / W, 5), round(y2 / H, 5)]],
                    "bbox_px": [x1, y1, x2, y2]}

        blocks = []
        for n, tr in zip(notes, translations):
            b = {**geom(n["box"]), "source": n["source"]}
            if n.get("color"):
                b["color"] = n["color"]  # text colour of a split speaker group (display-boosted), #rrggbb
            if translate:
                b["translation"] = tr  # None = the LLM gave nothing for it even after the retry
            blocks.append(b)
        untranslated = [{"id": i, "source": b["source"]} for i, b in enumerate(blocks)
                        if translate and b["translation"] is None]
        return {
            "width": W,
            "height": H,
            "blocks": blocks,
            "meta": {
                "timings_ms": timings,
                "threads": self.s.threads,
                "notes": [{"kind": n["kind"], "text_bbox_px": geom(n["text_box"])["bbox_px"],
                           "parts": len(n["parts"]), "ocr_tokens": n["ocr_usage"].get("prompt_tokens"),
                           **({"origin": n["origin"]} if "origin" in n else {}),
                           **({"upscale": n["upscale"]} if "upscale" in n else {}),
                           **({k: n[k] for k in ("color", "measured_color", "speaker", "split_from") if k in n})}
                          for n in notes],
                "skipped": [{**geom(d["box"]), "class": d["cls"], "reason": d["reason"],
                             **({"source": d["source"]} if "source" in d else {})} for d in skipped],
                "llm": {**llm_meta, "vision": self.s.vision} if translate else None,
                "untranslated": untranslated,
            },
        }
