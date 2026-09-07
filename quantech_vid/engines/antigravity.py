from __future__ import annotations

from collections.abc import Callable

from .common import ExecutableResolver, inspect_command_client, resolve_executable
from .models import (AuthStatus, ConnectionAction, EngineConnection, QuotaStatus,
                     RightsStatus)
from .process import BoundedProbeRunner, ProbeRunner


ANTIGRAVITY_DOCS = "https://antigravity.google/docs/cli/install/"


class AntigravityEngineAdapter:
    provider = "google_antigravity"

    def __init__(self, *, resolver: ExecutableResolver = resolve_executable,
                 runner: ProbeRunner | None = None) -> None:
        self.resolver = resolver
        self.runner = runner or BoundedProbeRunner()

    def inspect(self, *, timeout: float = 5.0,
                cancelled: Callable[[], bool] | None = None) -> EngineConnection:
        client, reasons, executable = inspect_command_client(
            command="agy", client_name="Google Antigravity CLI", expected_pattern=r"\b(?:agy|antigravity)\b",
            resolver=self.resolver, runner=self.runner, timeout=timeout, cancelled=cancelled)
        auth_reason = "AUTH_REQUIRES_INTERACTIVE_AGY" if executable else "CLIENT_NOT_INSTALLED"
        reasons.extend([auth_reason, "NATIVE_TOOL_ISOLATION_NOT_PROVEN"])
        return EngineConnection(
            provider=self.provider, client=client,
            auth=AuthStatus(state="unknown", method="google_account", reason_code=auth_reason),
            rights=RightsStatus(state="unknown", reason_code="ANTIGRAVITY_MODEL_RIGHTS_NOT_PROBED"),
            quota=QuotaStatus(state="unknown", reason_code="ANTIGRAVITY_QUOTA_NOT_PROBED"),
            reason_codes=list(dict.fromkeys(reasons)),
            connection_action=ConnectionAction(
                required=True, action_id="antigravity_interactive_keyring_login",
                human_click_required=True, documentation_url=ANTIGRAVITY_DOCS,
            ),
        )
