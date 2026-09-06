from __future__ import annotations

import os
import math
import numbers
import signal
import subprocess
import time
from collections.abc import Callable, Sequence


class _WindowsJob:
    """Windows process tree owner using JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not self._kernel32.SetInformationJobObject(
            self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process: subprocess.Popen[bytes]) -> bool:
        import ctypes
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            return True
        # A restrictive outer Job Object can reject nesting. Fall back to taskkill /T.
        if ctypes.get_last_error() == 5:
            self.close()
            return False
        error = ctypes.get_last_error()
        self.close()
        raise ctypes.WinError(error)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _taskkill_tree(pid: int) -> None:
    import ctypes
    system_directory = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(system_directory, len(system_directory)):
        raise OSError("Cannot resolve Windows system directory")
    helper = subprocess.Popen(
        [os.path.join(system_directory.value, "taskkill.exe"), "/PID", str(pid), "/T", "/F"],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        helper.wait(timeout=3)
    except subprocess.TimeoutExpired:
        helper.kill()
        try:
            helper.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _stop_process(process: subprocess.Popen[bytes], job: _WindowsJob | None = None) -> None:
    if job is not None:
        job.close()
    if os.name == "posix":
        # The leader may already have exited while a descendant still holds a pipe.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            _taskkill_tree(process.pid)
            if process.poll() is None:
                process.terminate()
        process.wait(timeout=2)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


def _drain_bounded(process: subprocess.Popen[bytes], timeout: float = 2.0) -> tuple[bytes, bytes]:
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        return b"", b""


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    cancelled: Callable[[], bool] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a bounded child process without a shell and always reap it."""
    if isinstance(args, (str, bytes, os.PathLike)) or not isinstance(args, Sequence):
        raise TypeError("Command arguments must be a sequence of strings, not a scalar")
    if not args:
        raise ValueError("Command arguments must not be empty")
    if isinstance(timeout, bool) or not isinstance(timeout, numbers.Real):
        raise TypeError("Command timeout must be a finite number")
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Command timeout must be finite and positive")

    if any(not isinstance(value, (str, os.PathLike)) for value in args):
        raise TypeError("Each command argument must be a string or path-like value")
    command = [os.fspath(value) for value in args]
    try:
        job = _WindowsJob() if os.name == "nt" else None
    except OSError:
        job = None
    try:
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is not None:
        try:
            if not job.assign(process):
                job = None
        except BaseException:
            _stop_process(process, job)
            _drain_bounded(process)
            raise
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancelled and cancelled():
                raise InterruptedError("Render cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Command timed out after {timeout:.1f} seconds")
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        _stop_process(process, job)
        _drain_bounded(process)
        raise
    finally:
        if job is not None:
            job.close()

    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )
    return result
