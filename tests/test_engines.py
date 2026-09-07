from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Sequence

import pytest
from pydantic import ValidationError

from quantech_vid.engines import (AntigravityEngineAdapter, CodexEngineAdapter,
                                  CopilotEngineAdapter, validate_artifact_receipt)
from quantech_vid.engines.process import (EngineCancelled, EngineProtocolError,
                                          EngineTimeout, BoundedProbeRunner,
                                          JsonlProcessClient, ProbeResult)


class FakeRunner:
    def __init__(self, stdout: str = "", *, error: Exception | None = None) -> None:
        self.stdout = stdout
        self.error = error
        self.calls: list[list[str]] = []

    def run(self, args: Sequence[str], *, timeout: float,
            cancelled: Callable[[], bool] | None = None) -> ProbeResult:
        self.calls.append(list(args))
        if self.error:
            raise self.error
        return ProbeResult(0, self.stdout)


class FakeRpc:
    def __init__(self, responses: dict[str, object] | None = None,
                 error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.error = error
        self.requests: list[tuple[str, dict]] = []
        self.notifications: list[tuple[str, dict]] = []
        self.closed = False

    def request(self, method: str, params: dict, *, timeout: float,
                cancelled: Callable[[], bool] | None = None) -> dict:
        self.requests.append((method, params))
        if self.error:
            raise self.error
        value = self.responses.get(method, {})
        if not isinstance(value, dict):
            raise EngineProtocolError("malformed fixture")
        return value

    def notify(self, method: str, params: dict) -> None:
        self.notifications.append((method, params))

    def close(self) -> None:
        self.closed = True


def test_codex_filters_account_identity_and_reads_quota_without_refresh() -> None:
    rpc = FakeRpc({
        "initialize": {"userAgent": "codex"},
        "account/read": {"account": {"type": "chatgpt", "email": "private@example.test",
                                      "accessToken": "never-return-this", "planType": "plus"},
                         "requiresOpenaiAuth": True},
        "account/rateLimits/read": {"rateLimits": {"primary": {"usedPercent": 25,
                                                                  "resetsAt": 1_900_000_000},
                                                    "rateLimitReachedType": None}},
    })
    adapter = CodexEngineAdapter(resolver=lambda _: "C:/bin/codex.exe",
                                 runner=FakeRunner("codex-cli 0.149.1"),
                                 rpc_factory=lambda _: rpc)
    connection = adapter.inspect()
    public = connection.model_dump(mode="json")
    encoded = json.dumps(public)
    assert connection.client.version.value == "0.149.1"
    assert connection.auth.state == "confirmed" and connection.auth.method == "chatgpt"
    assert connection.auth.expires_at is None
    assert connection.quota.state == "available" and connection.quota.remaining_percent == 75
    assert connection.rights.state == "unknown" and connection.production == "unavailable"
    assert ("account/read", {"refreshToken": False}) in rpc.requests
    assert all("login" not in method and "logout" not in method for method, _ in rpc.requests)
    assert "private@example.test" not in encoded and "never-return-this" not in encoded
    assert rpc.closed is True


@pytest.mark.parametrize("error,reason", [
    (EngineTimeout("late"), "AUTH_PROBE_TIMEOUT"),
    (EngineCancelled("cancelled"), "ENGINE_INSPECTION_CANCELLED"),
    (EngineProtocolError("bad"), "APP_SERVER_PROTOCOL_UNAVAILABLE"),
])
def test_codex_protocol_failures_are_closed_and_sanitized(error: Exception, reason: str) -> None:
    rpc = FakeRpc(error=error)
    connection = CodexEngineAdapter(
        resolver=lambda _: "codex", runner=FakeRunner("codex-cli 0.149.1"),
        rpc_factory=lambda _: rpc).inspect()
    assert connection.auth.state == "unknown"
    assert reason in connection.reason_codes
    assert rpc.closed is True


def test_old_auth_shape_is_not_misreported_as_authenticated() -> None:
    rpc = FakeRpc({"initialize": {}, "account/read": {"authMode": "chatgpt"}})
    connection = CodexEngineAdapter(
        resolver=lambda _: "codex", runner=FakeRunner("codex-cli 0.149.1"),
        rpc_factory=lambda _: rpc).inspect()
    assert connection.auth.state == "unknown"
    assert connection.auth.reason_code == "AUTH_RESPONSE_UNSUPPORTED"
    assert connection.quota.state == "unknown"


def test_absent_clients_and_optional_copilot_sdk_remain_distinct() -> None:
    missing_codex = CodexEngineAdapter(resolver=lambda _: None, runner=FakeRunner()).inspect()
    missing_agy = AntigravityEngineAdapter(resolver=lambda _: None, runner=FakeRunner()).inspect()
    copilot = CopilotEngineAdapter(package_resolver=lambda _: None,
                                   executable_resolver=lambda _: None,
                                   runner=FakeRunner()).inspect()
    assert missing_codex.client.installation == "missing"
    assert missing_agy.client.installation == "missing"
    assert copilot.client.installation == "missing" and copilot.client.runtime == "missing"
    assert all(item.auth.state == "unknown" for item in (missing_codex, missing_agy, copilot))
    assert all(item.quota.state == "unknown" for item in (missing_codex, missing_agy, copilot))


def test_provider_mismatch_and_probe_timeout_never_enable_production() -> None:
    mismatch = CodexEngineAdapter(resolver=lambda _: "codex",
                                  runner=FakeRunner("Google Antigravity 9.9")).inspect()
    timed = AntigravityEngineAdapter(resolver=lambda _: "agy",
                                     runner=FakeRunner(error=EngineTimeout("late"))).inspect()
    assert "PROVIDER_MISMATCH" in mismatch.reason_codes
    assert mismatch.auth.state == "unknown" and mismatch.production == "unavailable"
    assert "VERSION_PROBE_TIMEOUT" in timed.reason_codes
    assert timed.production == "unavailable"


def test_copilot_installed_sdk_does_not_guess_auth_rights_or_quota() -> None:
    connection = CopilotEngineAdapter(
        package_resolver=lambda _: "0.1.0",
        executable_resolver=lambda _: "copilot",
        runner=FakeRunner("GitHub Copilot CLI 1.2.3")).inspect()
    assert connection.client.installation == "installed"
    assert connection.client.version.value == "0.1.0"
    assert connection.client.runtime == "present"
    assert connection.auth.state == connection.rights.state == connection.quota.state == "unknown"
    assert connection.connection_action.human_click_required is True


def test_success_without_provider_bound_artifact_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_artifact_receipt("openai_codex", {"provider": "openai_codex", "status": "SUCCESS"})
    with pytest.raises(ValueError, match="provider mismatch"):
        validate_artifact_receipt("openai_codex", {
            "provider": "github_copilot", "artifact_id": "artifact-1", "artifact_sha256": "a" * 64})
    receipt = validate_artifact_receipt("openai_codex", {
        "provider": "openai_codex", "artifact_id": "artifact-1", "artifact_sha256": "a" * 64})
    assert receipt.artifact_id == "artifact-1"


def test_jsonl_process_rejects_malformed_output_timeout_and_cancel() -> None:
    malformed = JsonlProcessClient([sys.executable, "-u", "-c",
        "import sys,time; sys.stdin.readline(); print('not-json', flush=True); time.sleep(5)"])
    with pytest.raises(EngineProtocolError, match="malformed"):
        malformed.request("account/read", {}, timeout=1)
    assert malformed.process.poll() is not None

    sleeping = JsonlProcessClient([sys.executable, "-u", "-c",
        "import sys,time; sys.stdin.readline(); time.sleep(5)"])
    with pytest.raises(EngineTimeout):
        sleeping.request("account/read", {}, timeout=0.1)
    assert sleeping.process.poll() is not None

    cancelled = JsonlProcessClient([sys.executable, "-u", "-c",
        "import sys,time; sys.stdin.readline(); time.sleep(5)"])
    with pytest.raises(EngineCancelled):
        cancelled.request("account/read", {}, timeout=1, cancelled=lambda: True)
    assert cancelled.process.poll() is not None


def test_probe_enforces_output_limit_while_child_is_still_writing() -> None:
    runner = BoundedProbeRunner(max_output_bytes=1024)
    started = time.monotonic()
    with pytest.raises(EngineProtocolError, match="output exceeded"):
        runner.run([sys.executable, "-u", "-c",
                    "import sys,time; sys.stdout.buffer.write(b'x'*1000000); "
                    "sys.stdout.buffer.flush(); time.sleep(5)"], timeout=3)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_probe_stops_without_waiting_for_child_output(mode: str) -> None:
    runner = BoundedProbeRunner(max_output_bytes=1024)
    kwargs = ({"timeout": 0.1} if mode == "timeout" else
              {"timeout": 2, "cancelled": lambda: True})
    expected = EngineTimeout if mode == "timeout" else EngineCancelled
    started = time.monotonic()
    with pytest.raises(expected):
        runner.run([sys.executable, "-u", "-c", "import time; time.sleep(5)"], **kwargs)
    assert time.monotonic() - started < 2


def test_jsonl_rejects_oversized_line_and_reaps_reader() -> None:
    client = JsonlProcessClient(
        [sys.executable, "-u", "-c",
         "import sys,time; sys.stdin.readline(); print('x'*4096, flush=True); time.sleep(5)"],
        max_line_bytes=128,
    )
    with pytest.raises(EngineProtocolError, match="response exceeded"):
        client.request("account/read", {}, timeout=1)
    assert client.process.poll() is not None
    assert client._reader is not None and not client._reader.is_alive()


def test_jsonl_close_unblocks_reader_when_queue_is_full() -> None:
    client = JsonlProcessClient(
        [sys.executable, "-u", "-c",
         "import sys,time; [print('{\"id\":0,\"result\":{}}', flush=True) "
         "for _ in range(10000)]; time.sleep(5)"],
        max_line_bytes=256,
    )
    deadline = time.monotonic() + 2
    while client._messages.qsize() < client._messages.maxsize and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client._messages.full()
    started = time.monotonic()
    client.close()
    assert time.monotonic() - started < 2
    assert client.process.poll() is not None
    assert client._reader is not None and not client._reader.is_alive()
    assert not any(thread.name == "engine-probe-reader" and thread.is_alive()
                   for thread in threading.enumerate())


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_engine_process_rejects_non_positive_or_non_finite_timeout(timeout: float) -> None:
    runner = BoundedProbeRunner()
    with pytest.raises(ValueError, match="finite and positive"):
        runner.run([sys.executable, "-c", "pass"], timeout=timeout)
