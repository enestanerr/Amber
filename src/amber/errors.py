"""User-facing error hierarchy.

Every error shown to a user is an :class:`AmberError` with a one-line message, an optional
hint and a process exit code (``docs/03-cli-spec.md``):
``0`` ok · ``1`` error · ``2`` usage · ``3`` verify failed · ``4`` guard stopped the run ·
``5`` doctor found drift.
"""

from __future__ import annotations


class AmberError(Exception):
    """Base class: one-line message plus an actionable hint."""

    exit_code: int = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class UsageError(AmberError):
    """Wrong flags, missing arguments, invalid combinations."""

    exit_code = 2


class ConfigError(AmberError):
    """``amber.toml`` missing or invalid."""

    exit_code = 1


class VerifyFailed(AmberError):
    """Replay against a source trace diverged."""

    exit_code = 3


class GuardStopped(AmberError):
    """A guard stopped a live run."""

    exit_code = 4


class DoctorFoundDrift(AmberError):
    """``amber doctor`` reported a status other than healthy."""

    exit_code = 5
