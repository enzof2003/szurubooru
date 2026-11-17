"""childproc: a child started with spawn_tied() cannot outlive the process that started it.

Real processes (`sleep`, small Python scripts), Linux only: the Windows job-object path is not
exercised here.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import childproc  # noqa: E402


def running(pid: int) -> bool:
    """True while the process exists and is not a zombie (a killed child whose parent is gone
    stays a zombie until PID 1 reaps it; os.kill(pid, 0) would still succeed on it)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def wait_until(cond, timeout=10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# A stand-in for the service: starts a tied child, reports its pid, then waits (or exits).
PARENT = """
import sys, time, threading
sys.path.insert(0, {root!r})
import childproc
{setup}
proc = childproc.spawn_tied(["sleep", "300"])
print(proc.pid, flush=True)
{then}
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "PR_SET_PDEATHSIG is Linux-only")
class SpawnTiedTest(unittest.TestCase):
    def setUp(self):
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(10)

    def spawn(self, cmd, **kw) -> subprocess.Popen:
        p = childproc.spawn_tied(cmd, **kw)
        self.procs.append(p)
        return p

    def parent(self, then: str, setup: str = "") -> tuple[subprocess.Popen, int]:
        code = PARENT.format(root=str(ROOT), setup=setup, then=then)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True)
        self.procs.append(p)
        child = int(p.stdout.readline())
        self.addCleanup(lambda: running(child) and os.kill(child, signal.SIGKILL))
        self.assertTrue(running(child))
        return p, child

    # ---- start / stop

    def test_returns_a_running_popen(self):
        p = self.spawn(["sleep", "300"])
        self.assertIsInstance(p, subprocess.Popen)
        self.assertIsNone(p.poll())
        self.assertTrue(running(p.pid))

    def test_terminate_and_wait(self):
        p = self.spawn(["sleep", "300"])
        p.terminate()
        self.assertEqual(p.wait(10), -signal.SIGTERM)
        self.assertFalse(running(p.pid))  # reaped: no zombie left either

    def test_kill(self):
        p = self.spawn(["sleep", "300"])
        p.kill()
        self.assertEqual(p.wait(10), -signal.SIGKILL)

    def test_exit_code_of_a_child_that_ends_by_itself(self):
        p = self.spawn([sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertEqual(p.wait(10), 7)
        self.assertEqual(p.poll(), 7)

    def test_popen_arguments_are_passed_through(self):
        with tempfile.TemporaryFile() as out:
            p = self.spawn([sys.executable, "-c", "import os, sys; print(os.environ['MARK']); print('err', file=sys.stderr)"],
                           stdout=out, stderr=subprocess.STDOUT, env={**os.environ, "MARK": "passed-through"})
            self.assertEqual(p.wait(10), 0)
            out.seek(0)
            self.assertEqual(out.read().split(), [b"passed-through", b"err"])

    def test_missing_binary_raises_in_the_caller(self):
        before = threading.active_count()
        with self.assertRaises(FileNotFoundError):
            childproc.spawn_tied(["/nonexistent/llama-server"])
        self.assertTrue(wait_until(lambda: threading.active_count() <= before))  # the keeper thread ended

    def test_keeper_thread_ends_with_the_child(self):
        def keepers():
            return [t for t in threading.enumerate() if t.name == "child-keeper"]

        before = len(keepers())
        p = self.spawn(["sleep", "300"])
        self.assertEqual(len(keepers()), before + 1)
        self.assertTrue(all(t.daemon for t in keepers()))
        p.kill()
        p.wait(10)
        self.assertTrue(wait_until(lambda: len(keepers()) == before))

    def test_child_survives_the_thread_that_started_it(self):
        """PR_SET_PDEATHSIG fires when the forking *thread* exits. The service starts the OCR
        server from a worker thread, so the child must be forked from a thread of its own."""
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("p", self.spawn(["sleep", "300"])))
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive())
        time.sleep(0.5)
        self.assertIsNone(box["p"].poll())
        self.assertTrue(running(box["p"].pid))

    def test_several_children(self):
        procs = [self.spawn(["sleep", "300"]) for _ in range(3)]
        self.assertEqual(len({p.pid for p in procs}), 3)
        procs[1].kill()
        procs[1].wait(10)
        self.assertTrue(running(procs[0].pid) and running(procs[2].pid))

    # ---- no orphan

    def test_child_dies_when_the_parent_is_killed(self):
        """kill -9 / OOM kill / crash: no handler runs, the OS has to take the child down."""
        parent, child = self.parent("time.sleep(300)")
        parent.kill()
        parent.wait(10)
        self.assertTrue(wait_until(lambda: not running(child)), "orphan child still running")

    def test_child_dies_when_the_parent_exits_without_stopping_it(self):
        parent, child = self.parent("sys.exit(3)")
        self.assertEqual(parent.wait(10), 3)
        self.assertTrue(wait_until(lambda: not running(child)), "orphan child still running")

    def test_child_dies_when_the_parent_crashes(self):
        parent, child = self.parent("raise RuntimeError('boom')")
        self.assertEqual(parent.wait(10), 1)
        self.assertTrue(wait_until(lambda: not running(child)), "orphan child still running")

    def test_child_dies_when_the_parent_gets_sigterm(self):
        parent, child = self.parent("time.sleep(300)")
        parent.terminate()
        self.assertEqual(parent.wait(10), -signal.SIGTERM)
        self.assertTrue(wait_until(lambda: not running(child)), "orphan child still running")

    def test_child_started_from_a_worker_thread_dies_with_the_parent(self):
        code = PARENT.replace('proc = childproc.spawn_tied(["sleep", "300"])', """
box = {{}}
t = threading.Thread(target=lambda: box.setdefault("p", childproc.spawn_tied(["sleep", "300"])))
t.start(); t.join()
proc = box["p"]""").format(root=str(ROOT), setup="", then="time.sleep(300)")
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.procs.append(parent)
        child = int(parent.stdout.readline())
        self.addCleanup(lambda: running(child) and os.kill(child, signal.SIGKILL))
        time.sleep(0.5)
        self.assertTrue(running(child))  # the worker thread is gone, the child is not
        parent.kill()
        parent.wait(10)
        self.assertTrue(wait_until(lambda: not running(child)), "orphan child still running")

    def test_child_keeps_running_while_the_parent_lives(self):
        parent, child = self.parent("time.sleep(300)")
        time.sleep(1.0)
        self.assertIsNone(parent.poll())
        self.assertTrue(running(child))


@unittest.skipUnless(sys.platform.startswith("linux"), "signals as on the deployment")
class ExitOnSignalsTest(unittest.TestCase):
    """install_exit_on_signals(): SIGTERM/SIGHUP become SystemExit, so atexit handlers (the OCR
    server's stop()) run."""

    SCRIPT = """
import atexit, signal, sys, time
sys.path.insert(0, {root!r})
import childproc
atexit.register(lambda: open({marker!r}, "w").write("atexit ran"))
{setup}
childproc.install_exit_on_signals()
print("ready", flush=True)
time.sleep(300)
"""

    def run_script(self, sig, setup=""):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker")
            p = subprocess.Popen([sys.executable, "-c", self.SCRIPT.format(root=str(ROOT), marker=marker, setup=setup)],
                                 stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "ready")
                p.send_signal(sig)
                code = p.wait(10)
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait(10)
            return code, os.path.exists(marker)

    def test_sigterm_and_sighup_exit_through_atexit(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.assertEqual(self.run_script(sig), (128 + sig, True), sig)

    def test_without_it_sigterm_skips_atexit(self):
        # the reason the function exists
        code, ran = self.run_script(signal.SIGTERM, setup="childproc.install_exit_on_signals = lambda: None")
        self.assertEqual((code, ran), (-signal.SIGTERM, False))

    def test_existing_handlers_are_left_alone(self):
        # uvicorn installs its own SIGTERM handler (which runs the lifespan shutdown)
        setup = "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(42))"
        self.assertEqual(self.run_script(signal.SIGTERM, setup=setup), (42, True))

    def test_ignored_signals_stay_ignored(self):
        setup = "signal.signal(signal.SIGHUP, signal.SIG_IGN)"
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker")
            p = subprocess.Popen([sys.executable, "-c", self.SCRIPT.format(root=str(ROOT), marker=marker, setup=setup)],
                                 stdout=subprocess.PIPE, text=True)
            try:
                p.stdout.readline()
                p.send_signal(signal.SIGHUP)
                time.sleep(0.5)
                self.assertIsNone(p.poll())
            finally:
                p.kill()
                p.wait(10)

    def test_no_op_outside_the_main_thread(self):
        before = signal.getsignal(signal.SIGTERM)
        errors = []

        def call():
            try:
                childproc.install_exit_on_signals()
            except Exception as e:  # signal.signal raises ValueError off the main thread
                errors.append(e)

        t = threading.Thread(target=call)
        t.start()
        t.join(10)
        self.assertEqual(errors, [])
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


class OcrServerStopTest(unittest.TestCase):
    """OcrServer.stop() with a real child: terminate, then kill a child that ignores SIGTERM."""

    def server(self, cmd):
        import pipeline
        from types import SimpleNamespace
        s = pipeline.OcrServer(SimpleNamespace(ocr_port=1))
        s.proc = childproc.spawn_tied(cmd, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: s.proc and s.proc.poll() is None and s.proc.kill())
        return s

    def test_stop_terminates_and_reaps(self):
        s = self.server(["sleep", "300"])
        pid = s.proc.pid
        self.assertTrue(s.alive())
        s.stop()
        self.assertFalse(s.alive())
        self.assertIsNone(s.proc)
        self.assertFalse(running(pid))
        s.stop()  # idempotent

    def test_stop_kills_a_child_that_ignores_sigterm(self):
        import pipeline
        s = self.server([sys.executable, "-c",
                         "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(300)"])
        self.assertEqual(s.proc.stdout.readline().strip(), "ready")
        proc, pid = s.proc, s.proc.pid
        real_wait, waits = proc.wait, []

        def wait(timeout=None):  # don't sit through the real 10 s grace period
            waits.append(timeout)
            return real_wait(0.3 if len(waits) == 1 else timeout)

        proc.wait = wait
        s.stop()
        self.assertEqual(waits, [10, 10])  # grace period, then after the kill
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertFalse(running(pid))
        self.assertTrue(pipeline)

    def test_alive_follows_the_process(self):
        s = self.server([sys.executable, "-c", "pass"])
        s.proc.wait(10)
        self.assertFalse(s.alive())  # crashed/ended: PaddleOcr._url_key restarts it


if __name__ == "__main__":
    unittest.main()
