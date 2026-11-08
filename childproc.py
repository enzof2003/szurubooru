"""Start a child process that cannot outlive this one (no orphan llama-server).

Windows: the child is put in a Job Object with KILL_ON_JOB_CLOSE. The job handle is only held by
         this process, so when it exits for any reason (Ctrl+C, crash, taskkill /F) Windows kills the child.
Linux:   the child gets PR_SET_PDEATHSIG=SIGKILL. The signal fires when the *thread* that forked the
         child exits, so the child is spawned from a dedicated thread that lives exactly as long as it.
Both:    callers should still stop the child cleanly (atexit / lifespan); this is the hard-kill backstop.
No third-party imports, so it can be tested on its own.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading

_job = None  # Windows job handle; kept open for the whole life of the process


def _windows_job():
    global _job
    if _job is not None:
        return _job
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
        raise ctypes.WinError(ctypes.get_last_error())
    _job = (k32, job)
    return _job


def _spawn_windows(cmd, **kw) -> subprocess.Popen:
    k32, job = _windows_job()
    proc = subprocess.Popen(cmd, **kw)
    if not k32.AssignProcessToJobObject(job, int(proc._handle)):
        err = ctypes.WinError(ctypes.get_last_error())
        proc.kill()
        raise RuntimeError(f"could not tie child to this process (job object): {err}")
    return proc


def _spawn_posix(cmd, **kw) -> subprocess.Popen:
    parent = os.getpid()
    prctl = ctypes.CDLL(None, use_errno=True).prctl if sys.platform.startswith("linux") else None

    def child_setup():  # runs in the child between fork and exec; resolved in the parent beforehand
        if prctl is not None:
            prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
        if os.getppid() != parent:  # parent already died before prctl took effect
            os._exit(1)

    box: dict = {}
    ready = threading.Event()

    def keeper():  # forking thread == child's lifetime, so PDEATHSIG only fires when the process dies
        try:
            box["proc"] = subprocess.Popen(cmd, preexec_fn=child_setup, **kw)
        except BaseException as e:
            box["err"] = e
            ready.set()
            return
        ready.set()
        box["proc"].wait()

    threading.Thread(target=keeper, name="child-keeper", daemon=True).start()
    ready.wait()
    if "err" in box:
        raise box["err"]
    return box["proc"]


def spawn_tied(cmd, **kw) -> subprocess.Popen:
    """subprocess.Popen(cmd, **kw), but the child is killed by the OS if this process dies."""
    return _spawn_windows(cmd, **kw) if os.name == "nt" else _spawn_posix(cmd, **kw)


def install_exit_on_signals() -> None:
    """Turn SIGTERM/SIGHUP/SIGBREAK into SystemExit so atexit handlers run.

    Only replaces default handlers (uvicorn installs its own, which run the lifespan shutdown) and
    only from the main thread (signal.signal raises elsewhere)."""
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None and signal.getsignal(sig) is signal.SIG_DFL:
            signal.signal(sig, lambda signum, _f: sys.exit(128 + signum))
