from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from typing import Protocol

from .common import ExecutableResolver, inspect_command_client, resolve_executable
from .models import (AuthStatus, ClientStatus, ConnectionAction, EngineConnection,
                     QuotaStatus, RightsStatus, VersionStatus)
from .process import BoundedProbeRunner, ProbeRunner


COPILOT_DOCS = "https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/authenticate"


class PackageVersionResolver(Protocol):
    def __call__(self, distribution: str) -> str | None: ...


def package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


class CopilotEngineAdapter:
    provider = "github_copilot"

    def __init__(self, *, package_resolver: PackageVersionResolver = package_version,
                 executable_resolver: ExecutableResolver = resolve_executable,
                 runner: ProbeRunner | None = None) -> None:
        self.package_resolver = package_resolver
        self.executable_resolver = executable_resolver
        self.runner = runner or BoundedProbeRunner()

    def inspect(self, *, timeout: float = 5.0,
                cancelled: Callable[[], bool] | None = None) -> EngineConnection:
        sdk_version = self.package_resolver("github-copilot-sdk")
        _, runtime_reasons, executable = inspect_command_client(
            command="copilot", client_name="GitHub Copilot CLI runtime", expected_pattern=r"\bcopilot\b",
            resolver=self.executable_resolver, runner=self.runner, timeout=timeout, cancelled=cancelled)
        installed = sdk_version is not None
        client = ClientStatus(
            name="GitHub Copilot Python SDK", installation="installed" if installed else "missing",
            version=VersionStatus(state="known", value=sdk_version) if installed else VersionStatus(state="unknown"),
            runtime="present" if executable else "unknown" if installed else "missing",
        )
        reasons = ([] if installed else ["COPILOT_SDK_NOT_INSTALLED"]) + runtime_reasons
        reasons.extend(["AUTH_STATUS_API_NOT_INTEGRATED", "NATIVE_TOOL_ISOLATION_NOT_PROVEN",
                        "COPILOT_SDK_PUBLIC_PREVIEW"])
        return EngineConnection(
            provider=self.provider, client=client,
            auth=AuthStatus(state="unknown", method="github_oauth", reason_code="AUTH_STATUS_API_NOT_INTEGRATED"),
            rights=RightsStatus(state="unknown", reason_code="COPILOT_ENTITLEMENT_NOT_PROBED"),
            quota=QuotaStatus(state="unknown", reason_code="COPILOT_QUOTA_NOT_PROBED"),
            reason_codes=list(dict.fromkeys(reasons)),
            connection_action=ConnectionAction(
                required=True, action_id="copilot_interactive_github_oauth",
                human_click_required=True, documentation_url=COPILOT_DOCS,
            ),
        )
