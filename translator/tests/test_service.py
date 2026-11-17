"""Service-level guards: oversized images, OCR server restart. No fixtures, no models.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import pipeline  # noqa: E402


def png(size) -> bytes:
    buf = io.BytesIO()
    Image.new("L", size, 255).save(buf, format="PNG")
    return buf.getvalue()


class LoadImageTest(unittest.TestCase):
    def test_normal_image(self):
        img = pipeline.load_image(png((64, 48)))
        self.assertEqual((img.size, img.mode), ((64, 48), "RGB"))

    def test_too_many_pixels_refused_before_decoding(self):
        # 81 MP: over our limit, under Pillow's own error threshold (2x), where it only warns
        data = png((9000, 9001))
        with mock.patch.object(Image.Image, "convert", side_effect=AssertionError("decoded")):
            with self.assertRaises(pipeline.ImageTooLarge):
                pipeline.load_image(data)

    def test_limit_is_inclusive(self):
        with mock.patch.object(pipeline, "MAX_PIXELS", 64 * 48):
            pipeline.load_image(png((64, 48)))
            with self.assertRaises(pipeline.ImageTooLarge):
                pipeline.load_image(png((64, 49)))


class FakeProc:
    def __init__(self, code=None):
        self.returncode = code
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, -15

    def wait(self, timeout=None):
        return self.returncode

    kill = terminate


class OcrServerStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name in ("bin", "model", "mmproj"):
            (root / name).write_bytes(b"")
        s = SimpleNamespace(ocr_bin=root / "bin", ocr_model=root / "model", ocr_mmproj=root / "mmproj",
                            ocr_port=1, threads=1, ocr_gpu_layers=0)
        self.server = pipeline.OcrServer(s)
        self.log = root / "logs" / "ocr_server.log"
        patches = [
            mock.patch.object(pipeline.config, "ROOT", root),
            mock.patch.object(pipeline.requests, "get", side_effect=pipeline.requests.ConnectionError()),
            mock.patch.object(pipeline.childproc, "install_exit_on_signals"),
            mock.patch.object(pipeline.atexit, "register"),
            mock.patch.object(pipeline.time, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.atexit = pipeline.atexit.register

    def start(self, proc, **kw):
        with mock.patch.object(pipeline.childproc, "spawn_tied", return_value=proc):
            self.server.start(**kw)

    def test_not_ready_in_time_is_stopped(self):
        proc = FakeProc()
        orig = self.server._wait_ready
        with mock.patch.object(self.server, "_wait_ready", lambda health, log: orig(health, log, timeout=0)):
            with self.assertRaises(RuntimeError):
                self.start(proc)
        self.assertTrue(proc.terminated)       # not left running half-started
        self.assertFalse(self.server.alive())  # so the next request starts it again
        self.assertIsNone(self.server._log)

    def test_restart_keeps_the_old_log_and_closes_its_handle(self):
        with mock.patch.object(self.server, "_wait_ready"):
            self.start(FakeProc())
            first = self.server._log
            first.write(b"why it died\n")
            self.server.proc.returncode = 1  # crashed
            self.start(FakeProc())
        self.assertTrue(first.closed)
        self.assertIsNot(self.server._log, first)
        self.server.stop()
        self.assertIn(b"why it died", self.log.read_bytes())
        self.assertEqual(self.atexit.call_count, 1)  # registered once, not per restart


if __name__ == "__main__":
    unittest.main()
