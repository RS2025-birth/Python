from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from common_platform.config import get_settings
from sample_api.main import app


@pytest.fixture(autouse=True)
def mock_env_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """テスト実行用の必須環境変数をセットし、キャッシュをクリアする。"""
    monkeypatch.setenv("APP_EXTERNAL_API_BASE_URL", "https://example.com")
    monkeypatch.setenv("APP_AUTH_HEADER_VALUE", "Bearer secret_test_token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health_check_returns_ok_without_secrets() -> None:
    """GET /health が200を返し、秘密情報や内部構成を含まないことを検証する。"""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
    assert "secret_test_token" not in response.text
    assert "://example.com" not in response.text
