import pytest
from pydantic import HttpUrl, SecretStr, ValidationError

from common_platform.config import AppSettings


def test_config_valid_and_defaults(monkeypatch, tmp_path):
    # ローカルの.envを読み込まないよう空の一時ディレクトリへ移動
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("APP_EXTERNAL_API_BASE_URL", "https://bank-example.com")
    monkeypatch.setenv("APP_AUTH_HEADER_VALUE", "Bearer secret-token-value")
    monkeypatch.delenv("APP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("APP_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("APP_READ_TIMEOUT", raising=False)
    monkeypatch.delenv("APP_MAX_RETRIES", raising=False)
    monkeypatch.delenv("APP_AUTH_HEADER_NAME", raising=False)

    settings = AppSettings()  # type: ignore[call-arg]

    assert str(settings.external_api_base_url) == "https://bank-example.com/"
    assert settings.log_level == "INFO"
    assert settings.connect_timeout == 5.0
    assert settings.read_timeout == 30.0
    assert settings.max_retries == 3
    assert settings.auth_header_name == "Authorization"
    assert settings.auth_header_value.get_secret_value() == "Bearer secret-token-value"


def test_config_missing_required_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_EXTERNAL_API_BASE_URL", raising=False)
    monkeypatch.delenv("APP_AUTH_HEADER_VALUE", raising=False)

    with pytest.raises(ValidationError):
        AppSettings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("external_api_base_url", "not-a-valid-url"),
        ("external_api_base_url", "ftp://://example.com"),
        ("connect_timeout", 0),
        ("connect_timeout", -1.5),
        ("read_timeout", 0),
        ("read_timeout", -10.0),
        ("max_retries", -1),
        ("log_level", "VERBOSE"),
    ],
)
def test_config_invalid_values(monkeypatch, tmp_path, field_name: str, invalid_value):
    monkeypatch.chdir(tmp_path)
    base_kwargs = {
        "external_api_base_url": HttpUrl("https://bank-example.com"),
        "auth_header_value": SecretStr("Bearer valid-token"),
    }
    base_kwargs[field_name] = invalid_value

    with pytest.raises(ValidationError):
        AppSettings(**base_kwargs)  # type: ignore[arg-type]


def test_config_secret_value_not_exposed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    raw_secret = "SUPER_SECRET_BANK_API_KEY_987654321"
    settings = AppSettings(
        external_api_base_url=HttpUrl("https://bank-example.com"),
        auth_header_value=SecretStr(raw_secret),
    )

    assert raw_secret not in str(settings)
    assert raw_secret not in repr(settings)
    assert raw_secret not in str(settings.model_dump())
