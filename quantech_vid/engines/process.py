from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from quantech_vid.process import _WindowsJob, _stop_process


class EngineProcessError(RuntimeError):
    code = "ENGINE_PROCESS_ERROR"


class EngineTimeout(EngineProcessError):
    code = "ENGINE_TIMEOUT"


class EngineCancelled(EngineProcessError):
    code = "ENGINE_CANCELLED"


class EngineProtocolError(EngineProcessError):
    code = "ENGINE_PROTOCOL_ERROR"


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str


class ProbeRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout: float,
            cancelled: Callable[[], bool] | None = None) -> ProbeResult: ...


class JsonRpcClient(Protocol):
    def request(self, method: str, params: dict, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict: ...

    def notify(self, method: str, params: dict) -> None: ...

    def close(self) -> None: ...


def sanitized_child_environment() -> dict[str, str]:
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "PROGRAMDATA",
        "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME", "XDG_CONFIG_HOME",
        "XDG_DATA_HOME", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result["NO_COLOR"] = "1"
    return result


def _spawn(args: Sequence[str], *, stdin: int | None, stdout: int | None,
           stderr: int | None) -> tuple[subprocess.Popen[bytes], _WindowsJob | None]:
    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
        raise TypeError("process arguments must be a sequence of strings")
    if not args or any(not isinstance(value, str) or not value for value in args):
        raise TypeError("process arguments must be non-empty strings")
    try:
        job = _WindowsJob() if os.name == "nt" else None
    except OSError:
        job = None
    try:
        process = subprocess.Popen(
            list(args), shell=False, stdin=stdin, stdout=stdout, stderr=stderr,
            env=sanitized_child_environment(), start_new_session=os.name == "posix",
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
            raise
    return process, job


def _positive_finite_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("process timeout must be a finite number")
    value = float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("process timeout must be finite and positive")
    return value


def _close_stream(stream: object | None) -> None:
    if stream is not None:
        try:
            stream.close()  # type: ignore[union-attr]
        except OSError:
            pass


class BoundedProbeRunner:
    def __init__(self, max_output_bytes: int = 65_536) -> None:
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) \
                or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive integer")
        self.max_output_bytes = max_output_bytes

    def run(self, args: Sequence[str], *, timeout: float,
            cancelled: Callable[[], bool] | None = None) -> ProbeResult:
        timeout = _positive_finite_timeout(timeout)
        process, job = _spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        if process.stdout is None:
            _stop_process(process, job)
            raise EngineProtocolError("probe output pipe unavailable")
        output = bytearray()
        overflow = threading.Event()
        reader_done = threading.Event()

        def read_output() -> None:
            try:
                while True:
                    chunk = process.stdout.read1(8192)
                    if not chunk:
                        return
                    remaining = self.max_output_bytes + 1 - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    if len(output) > self.max_output_bytes:
                        overflow.set()
                        return
            except (OSError, ValueError):
                return
            finally:
                reader_done.set()

        reader = threading.Thread(target=read_output, name="engine-probe-reader", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled and cancelled():
                    raise EngineCancelled("probe cancelled")
                if overflow.is_set():
                    raise EngineProtocolError("probe output exceeded limit")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EngineTimeout("probe timed out")
                reader_done.wait(min(0.05, remaining))
            reader.join(timeout=1)
            if reader.is_alive():
                raise EngineProtocolError("probe output reader did not stop")
            if overflow.is_set():
                raise EngineProtocolError("probe output exceeded limit")
        except BaseException:
            _stop_process(process, job)
            _close_stream(process.stdout)
            reader.join(timeout=1)
            raise
        finally:
            if job is not None:
                job.close()
            _close_stream(process.stdout)
        return ProbeResult(process.returncode,
                           bytes(output).decode("utf-8", errors="replace").strip())


class JsonlProcessClient:
    def __init__(self, args: Sequence[str], *, max_line_bytes: int = 1_048_576) -> None:
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int) \
                or max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be a positive integer")
        self.max_line_bytes = max_line_bytes
        self._messages: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._closed = False
        self._reader: threading.Thread | None = None
        self.process, self.job = _spawn(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
        if self.process.stdin is None or self.process.stdout is None:
            self.close()
            raise EngineProtocolError("JSONL pipes unavailable")
        self._reader = threading.Thread(target=self._read_lines, daemon=True)
        self._reader.start()

    def _queue_message(self, message: bytes | None) -> bool:
        while not self._reader_stop.is_set():
            try:
                self._messages.put(message, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _read_lines(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                line = self.process.stdout.readline(self.max_line_bytes + 1)
                if not line:
                    break
                if not self._queue_message(line):
                    break
        finally:
            self._queue_message(None)

    def _send(self, message: dict) -> None:
        if self._closed or self.process.stdin is None:
            raise EngineProtocolError("JSONL process is closed")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > self.max_line_bytes:
            raise EngineProtocolError("JSONL request exceeded limit")
        with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise EngineProtocolError("JSONL write failed") from exc

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def request(self, method: str, params: dict, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict:
        timeout = _positive_finite_timeout(timeout)
        self._request_id += 1
        request_id = self._request_id
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + timeout
        skipped = 0
        while True:
            if cancelled and cancelled():
                self.close()
                raise EngineCancelled("JSONL request cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise EngineTimeout("JSONL request timed out")
            try:
                line = self._messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if line is None:
                self.close()
                raise EngineProtocolError("JSONL process ended before response")
            if len(line) > self.max_line_bytes:
                self.close()
                raise EngineProtocolError("JSONL response exceeded limit")
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.close()
                raise EngineProtocolError("malformed JSONL response") from exc
            if not isinstance(message, dict):
                self.close()
                raise EngineProtocolError("invalid JSONL response")
            if message.get("id") != request_id:
                skipped += 1
                if skipped > 128:
                    self.close()
                    raise EngineProtocolError("too many unrelated JSONL messages")
                continue
            if "error" in message or not isinstance(message.get("result"), dict):
                raise EngineProtocolError("JSON-RPC request failed")
            return message["result"]

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._reader_stop.set()
            _close_stream(self.process.stdin)
            _stop_process(self.process, self.job)
            self.job = None
            reader = self._reader
            if reader is not None:
                reader.join(timeout=1)
                if reader.is_alive():
                    _close_stream(self.process.stdout)
                    reader.join(timeout=1)
            _close_stream(self.process.stdout)

    def __enter__(self) -> "JsonlProcessClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
