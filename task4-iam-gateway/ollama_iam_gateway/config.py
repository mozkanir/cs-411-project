"""Gateway configuration.

All settings come from environment variables with the prefix ``IAM_``.
Defaults are safe for local development.

Example (PowerShell):
    $env:IAM_DB_URL = "sqlite:///./iam.db"
    $env:IAM_OLLAMA_URL = "http://127.0.0.1:11434"
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Storage
    db_url: str = "sqlite:///./iam.db"

    # Upstream Ollama server (must listen on loopback only, see deployment view)
    ollama_url: str = "http://127.0.0.1:11434"

    # Where the gateway itself listens
    host: str = "127.0.0.1"
    port: int = 8080

    # Sessions (FR2)
    session_idle_minutes: int = 30
    session_max_hours: int = 8

    # Account lockout (FR3)
    lockout_threshold: int = 5
    lockout_minutes: int = 15

    # Passwords
    min_password_length: int = 8

    # Set to True when the gateway is served over HTTPS
    cookie_secure: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            db_url=_env_str("IAM_DB_URL", d.db_url),
            ollama_url=_env_str("IAM_OLLAMA_URL", d.ollama_url).rstrip("/"),
            host=_env_str("IAM_HOST", d.host),
            port=_env_int("IAM_PORT", d.port),
            session_idle_minutes=_env_int("IAM_SESSION_IDLE_MINUTES", d.session_idle_minutes),
            session_max_hours=_env_int("IAM_SESSION_MAX_HOURS", d.session_max_hours),
            lockout_threshold=_env_int("IAM_LOCKOUT_THRESHOLD", d.lockout_threshold),
            lockout_minutes=_env_int("IAM_LOCKOUT_MINUTES", d.lockout_minutes),
            min_password_length=_env_int("IAM_MIN_PASSWORD_LENGTH", d.min_password_length),
            cookie_secure=_env_bool("IAM_COOKIE_SECURE", d.cookie_secure),
        )
