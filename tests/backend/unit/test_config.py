import pytest

from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.domain.errors import ValidationError


def test_defaults_when_only_narrator_model_set():
    settings = load_settings({"RPG_NARRATOR_MODEL": "my-narrator"})
    assert settings.narrator.model == "my-narrator"
    assert settings.narrator.base_url == "http://127.0.0.1:5000/v1"
    assert settings.narrator.temperature == 0.8
    assert settings.critic is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.max_history_messages == 40


def test_critic_enabled_only_when_model_set_and_inherits_connection():
    settings = load_settings({
        "RPG_NARRATOR_MODEL": "narr",
        "RPG_NARRATOR_BASE_URL": "http://localhost:1234/v1",
        "RPG_NARRATOR_API_KEY": "secret",
        "RPG_CRITIC_MODEL": "critic-7b",
        "RPG_CRITIC_TEMPERATURE": "0.2",
    })
    assert settings.critic is not None
    assert settings.critic.model == "critic-7b"
    assert settings.critic.base_url == "http://localhost:1234/v1"
    assert settings.critic.api_key == "secret"
    assert settings.critic.temperature == 0.2


def test_invalid_numbers_and_port_rejected():
    with pytest.raises(ValidationError, match="RPG_NARRATOR_TEMPERATURE"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_NARRATOR_TEMPERATURE": "hot"})
    with pytest.raises(ValidationError, match="temperature"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_NARRATOR_TEMPERATURE": "9"})
    with pytest.raises(ValidationError, match="RPG_SERVER_PORT"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_SERVER_PORT": "0"})
    with pytest.raises(ValidationError, match="RPG_NARRATOR_MODEL"):
        load_settings({"RPG_NARRATOR_MODEL": "  "})
