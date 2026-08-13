"""Environment-based settings for the server and the two model roles."""

from dataclasses import dataclass
import os

from .domain.errors import ValidationError

_DEFAULT_BASE_URL = "http://127.0.0.1:5000/v1"


@dataclass(frozen=True)
class ModelConfig:
    """Connection parameters for one OpenAI-compatible model endpoint."""

    base_url: str
    api_key: str
    model: str
    temperature: float
    timeout_seconds: float
    max_tokens: int


@dataclass(frozen=True)
class Settings:
    """Runtime configuration; ``critic`` is None in single-model mode."""

    database_path: str
    host: str
    port: int
    max_history_messages: int
    narrator: ModelConfig
    critic: ModelConfig | None


def _float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValidationError(f"{key} must be a number") from None


def _int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValidationError(f"{key} must be an integer") from None


def _model(
    env: dict[str, str], prefix: str, fallback: ModelConfig | None
) -> ModelConfig | None:
    model = env.get(f"{prefix}_MODEL", "").strip()
    if not model:
        if prefix == "RPG_NARRATOR":
            raise ValidationError("RPG_NARRATOR_MODEL must not be empty")
        return None
    base_url = env.get(f"{prefix}_BASE_URL") or (
        fallback.base_url if fallback else _DEFAULT_BASE_URL
    )
    api_key = env.get(f"{prefix}_API_KEY") or (fallback.api_key if fallback else "")
    temperature = _float(env, f"{prefix}_TEMPERATURE", 0.8)
    if not 0.0 <= temperature <= 2.0:
        raise ValidationError(f"{prefix} temperature must be within 0..2")
    timeout = _float(env, f"{prefix}_TIMEOUT_SECONDS", 120.0)
    if timeout <= 0:
        raise ValidationError(f"{prefix} timeout must be positive")
    max_tokens = _int(env, f"{prefix}_MAX_TOKENS", 1024)
    if max_tokens < 1:
        raise ValidationError(f"{prefix} max_tokens must be positive")
    return ModelConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout,
        max_tokens=max_tokens,
    )


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from ``env`` (defaults to ``os.environ``)."""
    env = os.environ if env is None else env
    narrator = _model(env, "RPG_NARRATOR", None)
    port = _int(env, "RPG_SERVER_PORT", 8000)
    if not 1 <= port <= 65535:
        raise ValidationError("RPG_SERVER_PORT must be within 1..65535")
    max_history = _int(env, "RPG_MAX_HISTORY_MESSAGES", 40)
    if max_history < 1:
        raise ValidationError("RPG_MAX_HISTORY_MESSAGES must be positive")
    return Settings(
        database_path=env.get("RPG_DATABASE", "./rpg.sqlite3"),
        host=env.get("RPG_SERVER_HOST", "127.0.0.1"),
        port=port,
        max_history_messages=max_history,
        narrator=narrator,
        critic=_model(env, "RPG_CRITIC", narrator),
    )
