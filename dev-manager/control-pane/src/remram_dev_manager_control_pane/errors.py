from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import exit_codes
from .models import ErrorPayload


@dataclass
class RemramError(Exception):
    error_type: str
    error_message: str
    recovery_message: str
    exit_code: int = exit_codes.GENERIC_FAILURE
    details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return ErrorPayload(
            error_type=self.error_type,
            error_message=self.error_message,
            recovery_message=self.recovery_message,
            details=self.details,
        ).as_dict()


class ConfigError(RemramError):
    def __init__(self, error_message: str, recovery_message: str, **details: Any) -> None:
        super().__init__(
            error_type="config_error",
            error_message=error_message,
            recovery_message=recovery_message,
            exit_code=exit_codes.CONFIG_ERROR,
            details=details,
        )


class ValidationError(RemramError):
    def __init__(self, error_message: str, recovery_message: str, **details: Any) -> None:
        super().__init__(
            error_type="validation_failure",
            error_message=error_message,
            recovery_message=recovery_message,
            exit_code=exit_codes.VALIDATION_FAILURE,
            details=details,
        )


class TargetNotFoundError(RemramError):
    def __init__(self, target: str) -> None:
        super().__init__(
            error_type="target_not_found",
            error_message=f"target '{target}' not registered",
            recovery_message="run `remram list-targets` to see available targets",
            exit_code=exit_codes.TARGET_NOT_FOUND,
            details={"target": target},
        )


class ControlPlaneUnavailableError(RemramError):
    def __init__(self, error_message: str, recovery_message: str, **details: Any) -> None:
        super().__init__(
            error_type="control_plane_unavailable",
            error_message=error_message,
            recovery_message=recovery_message,
            exit_code=exit_codes.CONTROL_PLANE_UNAVAILABLE,
            details=details,
        )
