from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .common import ExecutableResolver, inspect_command_client, resolve_executable
from .models import (AuthStatus, ConnectionAction, EngineConnection, QuotaStatus,
                     RightsStatus)
from .process import (BoundedProbeRunner, EngineCancelled, EngineProcessError,
                      EngineTimeout, JsonRpcClient, JsonlProcessClient, ProbeRunner)


CODEX_DOCS = "https://learn.chatgpt.com/docs/app-server"


class RpcFactory(Protocol):
    def __call__(self, args: list[str]) -> JsonRpcClient: ...


def _auth_status(result: object) -> AuthStatus:
    if not isinstance(result, dict) or "account" not in result:
        return AuthStatus(state="unknown", reason_code="AUTH_RESPONSE_UNSUPPORTED")
    account = result.get("account")
    if account is None:
        if result.get("requiresOpenaiAuth") is True:
            return AuthStatus(state="unauthenticated", reason_code="OPENAI_AUTH_REQUIRED")
        return AuthStatus(state="unknown", reason_code="NON_OPENAI_CREDENTIAL_STATE_UNVERIFIED")
    if not isinstance(account, dict) or not isinstance(account.get("type"), str):
        return AuthStatus(state="unknown", reason_code="AUTH_RESPONSE_UNSUPPORTED")
    method = "chatgpt" if account["type"] == "chatgpt" else "api_key" if account["type"] == "apiKey" else "unknown"
    return AuthStatus(state="confirmed", method=method, reason_code="ACCOUNT_READ_CONFIRMED")


def _quota_status(result: object) -> QuotaStatus:
    if not isinstance(result, dict) or not isinstance(result.get("rateLimits"), dict):
        return QuotaStatus(state="unknown", reason_code="QUOTA_RESPONSE_UNAVAILABLE")
    limits = result["rateLimits"]
    primary = limits.get("primary")
    if not isinstance(primary, dict) or not isinstance(primary.get("usedPercent"), (int, float)):
        return QuotaStatus(state="unknown", reason_code="QUOTA_RESPONSE_UNAVAILABLE")
    used = max(0.0, min(100.0, float(primary["usedPercent"])))
    exhausted = used >= 100 or limits.get("rateLimitReachedType") is not None
    reset = primary.get("resetsAt") if isinstance(primary.get("resetsAt"), int) else None
    return QuotaStatus(state="exhausted" if exhausted else "available",
                       remaining_percent=100.0 - used, resets_at=reset,
                       reason_code="RATE_LIMIT_REACHED" if exhausted else "RATE_LIMIT_READ_CONFIRMED")


class CodexEngineAdapter:
    provider = "openai_codex"

    def __init__(self, *, resolver: ExecutableResolver = resolve_executable,
                 runner: ProbeRunner | None = None, rpc_factory: RpcFactory = JsonlProcessClient) -> None:
        self.resolver = resolver
        self.runner = runner or BoundedProbeRunner()
        self.rpc_factory = rpc_factory

    def inspect(self, *, timeout: float = 5.0,
                cancelled: Callable[[], bool] | None = None) -> EngineConnection:
        client, reasons, executable = inspect_command_client(
            command="codex", client_name="Codex CLI App Server", expected_pattern=r"\bcodex(?:-cli)?\b",
            resolver=self.resolver, runner=self.runner, timeout=timeout, cancelled=cancelled)
        auth = AuthStatus(state="unknown", reason_code="CLIENT_NOT_INSTALLED")
        quota = QuotaStatus(state="unknown", reason_code="CLIENT_NOT_INSTALLED")
        if executable is not None and "PROVIDER_MISMATCH" not in reasons:
            rpc: JsonRpcClient | None = None
            try:
                rpc = self.rpc_factory([executable, "app-server", "--stdio"])
                initialized = rpc.request("initialize", {"clientInfo": {
                    "name": "quantech_vid", "title": "QuaNTecH-ViD", "version": "2.1.0",
                }}, timeout=timeout, cancelled=cancelled)
                if not isinstance(initialized, dict):
                    raise EngineProcessError("invalid initialize response")
                rpc.notify("initialized", {})
                account = rpc.request("account/read", {"refreshToken": False}, timeout=timeout,
                                      cancelled=cancelled)
                auth = _auth_status(account)
                if auth.state == "confirmed" and auth.method == "chatgpt":
                    quota = _quota_status(rpc.request("account/rateLimits/read", {}, timeout=timeout,
                                                     cancelled=cancelled))
                else:
                    quota = QuotaStatus(state="unknown", reason_code="QUOTA_NOT_READ_FOR_AUTH_MODE")
            except EngineTimeout:
                reasons.append("AUTH_PROBE_TIMEOUT")
                auth = AuthStatus(state="unknown", reason_code="AUTH_PROBE_TIMEOUT")
                quota = QuotaStatus(state="unknown", reason_code="AUTH_PROBE_TIMEOUT")
            except EngineCancelled:
                reasons.append("ENGINE_INSPECTION_CANCELLED")
                auth = AuthStatus(state="unknown", reason_code="ENGINE_INSPECTION_CANCELLED")
                quota = QuotaStatus(state="unknown", reason_code="ENGINE_INSPECTION_CANCELLED")
            except (EngineProcessError, OSError):
                reasons.append("APP_SERVER_PROTOCOL_UNAVAILABLE")
                auth = AuthStatus(state="unknown", reason_code="APP_SERVER_PROTOCOL_UNAVAILABLE")
                quota = QuotaStatus(state="unknown", reason_code="APP_SERVER_PROTOCOL_UNAVAILABLE")
            finally:
                if rpc is not None:
                    rpc.close()
        reasons.append("NATIVE_TOOL_ISOLATION_NOT_PROVEN")
        action_required = auth.state != "confirmed"
        return EngineConnection(
            provider=self.provider, client=client, auth=auth,
            rights=RightsStatus(state="unknown", reason_code="MODEL_RIGHTS_NOT_PROBED"),
            quota=quota, reason_codes=list(dict.fromkeys(reasons)),
            connection_action=ConnectionAction(
                required=action_required,
                action_id="codex_managed_chatgpt_login" if action_required else None,
                human_click_required=action_required, documentation_url=CODEX_DOCS,
            ),
        )
