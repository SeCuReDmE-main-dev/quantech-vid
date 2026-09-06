from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from typing import Protocol

from .models import ClientStatus, VersionStatus
from .process import EngineCancelled, EngineProcessError, EngineTimeout, ProbeRunner


class ExecutableResolver(Protocol):
    def __call__(self, command: str) -> str | None: ...


def resolve_executable(command: str) -> str | None:
    return shutil.which(command)


def inspect_command_client(*, command: str, client_name: str, expected_pattern: str,
                           resolver: ExecutableResolver, runner: ProbeRunner,
                           timeout: float, cancelled: Callable[[], bool] | None) -> tuple[ClientStatus, list[str], str | None]:
    executable = resolver(command)
    if executable is None:
        return (ClientStatus(name=client_name, installation="missing",
                             version=VersionStatus(state="unknown"), runtime="missing"),
                ["CLIENT_NOT_INSTALLED"], None)
    reasons: list[str] = []
    try:
        result = runner.run([executable, "--version"], timeout=timeout, cancelled=cancelled)
        if result.returncode != 0:
            reasons.append("VERSION_PROBE_FAILED")
            version = VersionStatus(state="unknown")
        elif not re.search(expected_pattern, result.stdout, flags=re.IGNORECASE):
            reasons.append("PROVIDER_MISMATCH")
            version = VersionStatus(state="unknown")
        else:
            match = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?", result.stdout)
            if match:
                version = VersionStatus(state="known", value=match.group(0))
            else:
                reasons.append("VERSION_UNKNOWN")
                version = VersionStatus(state="unknown")
    except EngineTimeout:
        reasons.append("VERSION_PROBE_TIMEOUT")
        version = VersionStatus(state="unknown")
    except EngineCancelled:
        reasons.append("ENGINE_INSPECTION_CANCELLED")
        version = VersionStatus(state="unknown")
    except (EngineProcessError, OSError):
        reasons.append("VERSION_PROBE_FAILED")
        version = VersionStatus(state="unknown")
    return ClientStatus(name=client_name, installation="installed", version=version,
                        runtime="present"), reasons, executable
