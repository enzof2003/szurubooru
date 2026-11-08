"""CPU benchmark of detection + OCR (no LLM), with peak RAM of the service and its OCR server.

  THREADS=4 python bench.py --out bench/cpu_t4.json "translate-test/*"

Run each thread count in a fresh process: the OCR server starts empty, so llama-server's prompt
cache cannot make repeated crops look faster than they are.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import config

import psutil
from PIL import ImageOps

from pipeline import Pipeline, load_image


class PeakRss(threading.Thread):
    """Samples RSS of this process and the OCR server every 50 ms (Windows: also peak working set)."""

    def __init__(self, pids):
        super().__init__(daemon=True)
        self.procs = {name: psutil.Process(pid) for name, pid in pids.items()}
        self.peak = {name: 0 for name in pids}
        self.peak_sum = 0  # both processes at the same instant (what the VPS has to hold)
        self.last = {name: 0 for name in pids}
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            for name, p in self.procs.items():
                try:
                    mi = p.memory_info()
                    self.peak[name] = max(self.peak[name], getattr(mi, "peak_wset", 0) or 0, mi.rss)
                    self.last[name] = mi.rss
                except psutil.Error:
                    pass
            self.peak_sum = max(self.peak_sum, sum(self.last.values()))
            time.sleep(0.05)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out")
    args = ap.parse_args()

    s = config.load()
    t0 = time.perf_counter()
    p = Pipeline(s)
    startup_s = time.perf_counter() - t0
    assert "torch" not in sys.modules, "service must not import torch"
    mon = PeakRss({"service": psutil.Process().pid, "ocr_server": p.ocr_server.proc.pid})
    mon.start()
    try:
        paths = [Path(x) for pat in args.images for x in sorted(glob.glob(pat))
                 if Path(x).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and "_preview" not in x]
        p.detect_and_ocr(ImageOps.mirror(load_image(paths[0].read_bytes())))  # warm-up, not cache-identical

        rows = []
        for path in paths:
            img = load_image(path.read_bytes())
            t = time.perf_counter()
            notes, skipped, tm = p.detect_and_ocr(img)
            total = (time.perf_counter() - t) * 1000
            rows.append({"image": path.name, "size": f"{img.width}x{img.height}", "notes": len(notes),
                         "skipped": len(skipped), "detect_ms": tm["detect_ms"], "ocr_ms": tm["ocr_ms"],
                         "ocr_ms_per_note": tm["ocr_ms_per_note"], "total_ms": round(total, 1)})
            print(json.dumps(rows[-1]), flush=True)
    finally:
        time.sleep(0.5)
        idle = dict(mon.last)
        mon.stop_flag = True
        mon.join()
        p.close()

    per_note = [x for r in rows for x in r["ocr_ms_per_note"]]
    summary = {
        "threads": s.threads, "startup_s": round(startup_s, 1),
        "detect_ms_median": statistics.median(r["detect_ms"] for r in rows),
        "detect_ms_max": max(r["detect_ms"] for r in rows),
        "ocr_ms_per_note_median": statistics.median(per_note), "ocr_ms_per_note_max": max(per_note),
        "total_ms_median": statistics.median(r["total_ms"] for r in rows),
        "total_ms_max": max(r["total_ms"] for r in rows),
        "peak_rss_mb": {k: round(v / 2**20) for k, v in mon.peak.items()},
        "peak_combined_mb": round(mon.peak_sum / 2**20),
        "idle_rss_mb": {k: round(v / 2**20) for k, v in idle.items()},
    }
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "images": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
