from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionStatus(ClosedModel):
    state: Literal["known", "unknown"]
    value: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def value_matches_state(self) -> "VersionStatus":
        if (self.state == "known") != (self.value is not None):
            raise ValueError("known versions require a value")
        return self


class ClientStatus(ClosedModel):
    name: str = Field(min_length=1, max_length=80)
    installation: Literal["installed", "missing"]
    version: VersionStatus
    runtime: Literal["present", "missing", "unknown"]


class AuthStatus(ClosedModel):
    state: Literal["confirmed", "unauthenticated", "expired", "unknown"]
    method: Literal["chatgpt", "api_key", "github_oauth", "google_account", "unknown"] = "unknown"
    expires_at: str | None = Field(default=None, max_length=80)
    reason_code: str = Field(min_length=1, max_length=100)


class RightsStatus(ClosedModel):
    state: Literal["confirmed", "denied", "unknown"]
    scopes: list[str] = Field(default_factory=list, max_length=32)
    reason_code: str = Field(min_length=1, max_length=100)


class QuotaStatus(ClosedModel):
    state: Literal["available", "exhausted", "unknown"]
    remaining_percent: float | None = Field(default=None, ge=0, le=100)
    resets_at: int | None = Field(default=None, ge=0)
    reason_code: str = Field(min_length=1, max_length=100)


class ConnectionAction(ClosedModel):
    required: bool
    action_id: str | None = Field(default=None, max_length=100)
    human_click_required: bool
    documentation_url: str = Field(pattern=r"^https://")


class EngineConnection(ClosedModel):
    provider: Literal["openai_codex", "github_copilot", "google_antigravity"]
    client: ClientStatus
    auth: AuthStatus
    rights: RightsStatus
    quota: QuotaStatus
    production: Literal["unavailable"] = "unavailable"
    reason_codes: list[str] = Field(min_length=1, max_length=16)
    connection_action: ConnectionAction


class EngineArtifactReceipt(ClosedModel):
    provider: Literal["openai_codex", "github_copilot", "google_antigravity"]
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_artifact_receipt(provider: str, payload: object) -> EngineArtifactReceipt:
    """Require provider-bound artifact evidence; a success label alone is never success."""
    receipt = EngineArtifactReceipt.model_validate(payload)
    if receipt.provider != provider:
        raise ValueError("provider mismatch")
    return receipt
