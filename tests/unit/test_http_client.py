import json
import logging

import httpx
import pytest
from pydantic import HttpUrl, SecretStr

from common_platform.auth import StaticHeaderAuthAdapter
from common_platform.config import AppSettings
from common_platform.correlation import set_correlation_id
from common_platform.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    ExternalServiceUnavailableError,
)
from common_platform.http_client import (
    AsyncExternalHttpClient,
    create_external_client_from_settings,
)
from common_platform.logger import JsonFormatter


def test_create_external_client_from_settings():
    settings = AppSettings(
        external_api_base_url=HttpUrl("https://example.com"),
        connect_timeout=3.5,
        read_timeout=15.0,
        max_retries=2,
        auth_header_name="Authorization",
        auth_header_value=SecretStr("Bearer test-bank-token"),
    )
    client = create_external_client_from_settings(settings)

    assert client.base_url == "https://example.com"
    assert client.max_retries == 2
    assert client.auth_adapter is not None
    assert client.auth_adapter.get_headers() == {
        "Authorization": "Bearer test-bank-token"
    }


async def test_get_success_with_headers_and_logs(caplog):
    caplog.set_level(logging.INFO, logger="common_platform")
    cid = set_correlation_id("corr-http-123")
    captured_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(
            200, json={"result": "ok", "secret_payload": "TOP_SECRET_RESP"}
        )

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    auth = StaticHeaderAuthAdapter("X-API-Key", "my-super-secret-key")
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        auth_adapter=auth,
        client=mock_client,
    )

    data = await client.get("/v1/items", params={"api_token": "secret_query_token"})
    assert data == {"result": "ok", "secret_payload": "TOP_SECRET_RESP"}

    # 相関IDと認証ヘッダーが付与されていること
    assert captured_headers["x-correlation-id"] == cid
    assert captured_headers["x-api-key"] == "my-super-secret-key"

    # ログ検証（開始・完了が出力され、秘密情報・クエリ・応答本文が含まれないこと）
    formatter = JsonFormatter()
    logs = [
        json.loads(formatter.format(r))
        for r in caplog.records
        if r.name == "common_platform"
    ]
    assert [log["event"] for log in logs] == [
        "external_request_started",
        "external_request_completed",
    ]
    raw_logs = json.dumps(logs)
    assert "my-super-secret-key" not in raw_logs
    assert "secret_query_token" not in raw_logs
    assert "TOP_SECRET_RESP" not in raw_logs


async def test_get_retries_on_503_and_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"recovered": True})

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        max_retries=2,
        backoff_base=0.01,  # テスト高速化のため短縮
        client=mock_client,
    )

    data = await client.get("/v1/retry-test")
    assert data == {"recovered": True}
    assert attempts == 3


async def test_post_does_not_retry_on_503():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": "unavailable"})

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        max_retries=3,
        backoff_base=0.01,
        client=mock_client,
    )

    with pytest.raises(ExternalServiceUnavailableError):
        await client.post("/v1/orders", json_body={"amount": 1000})

    # POSTは再試行せず1回で終了すること
    assert attempts == 1


async def test_http_error_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/401":
            return httpx.Response(401)
        if request.url.path == "/400":
            return httpx.Response(400)
        raise httpx.ReadTimeout("timed out")

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        max_retries=0,
        client=mock_client,
    )

    with pytest.raises(AuthenticationError):
        await client.get("/401")

    with pytest.raises(ExternalServiceError):
        await client.get("/400")

    with pytest.raises(ExternalServiceUnavailableError):
        await client.get("/timeout")


async def test_get_does_not_retry_on_non_retryable_status():
    """GETであっても400や404などの再試行対象外ステータスでは再試行しないことを確認する。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, json={"error": "not found"})

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        max_retries=3,
        backoff_base=0.01,
        client=mock_client,
    )

    with pytest.raises(ExternalServiceError):
        await client.get("/v1/missing-resource")

    assert attempts == 1


async def test_get_retries_on_read_timeout_and_fails_after_max():
    """読取タイムアウト時は最大再試行回数までリトライし、上限到達後に503相当の例外を送出する。"""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("read timed out")

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        max_retries=2,
        backoff_base=0.01,
        client=mock_client,
    )

    with pytest.raises(ExternalServiceUnavailableError):
        await client.get("/v1/slow-endpoint")

    # 初回1回 + リトライ2回 = 計3回試行されること
    assert attempts == 3


async def test_get_204_no_content_returns_none():
    """HTTP 204 No Content の場合にNoneが返ることを確認する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    async with AsyncExternalHttpClient(
        base_url="https://example.com",
        client=mock_client,
    ) as client:
        result = await client.get("/v1/empty")

    assert result is None


async def test_generic_request_error_mapped_to_external_service_error():
    """ConnectError/Timeout以外の汎用httpx.RequestErrorがExternalServiceErrorに変換されることを確認する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProtocolError("Malformed HTTP protocol")

    mock_client = httpx.AsyncClient(
        base_url="https://example.com",
        transport=httpx.MockTransport(handler),
    )
    client = AsyncExternalHttpClient(
        base_url="https://example.com",
        client=mock_client,
    )

    with pytest.raises(ExternalServiceError):
        await client.get("/v1/protocol-error")
