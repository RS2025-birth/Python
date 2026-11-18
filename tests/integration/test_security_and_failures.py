import json
import logging
from collections.abc import AsyncGenerator

import httpx
import pytest
from apps.sample_api.main import app as sample_app
from apps.sample_api.main import get_external_client

from common_platform.auth import StaticHeaderAuthAdapter
from common_platform.http_client import AsyncExternalHttpClient
from common_platform.logger import JsonFormatter


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    sample_app.dependency_overrides.clear()


def _collect_all_logs(caplog) -> str:
    formatter = JsonFormatter()
    lines = [
        formatter.format(record)
        for record in caplog.records
        if record.name == "common_platform"
    ]
    return "\n".join(lines)


async def test_integration_health_check():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "X-Correlation-ID" in response.headers


async def test_integration_connection_failure_returns_503():
    """外部サービスへの接続失敗（ConnectError）時に503標準エラーが返ることを確認する。"""

    def connect_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async def override_connect_error_client() -> AsyncGenerator[
        AsyncExternalHttpClient
    ]:
        raw_httpx = httpx.AsyncClient(
            transport=httpx.MockTransport(connect_error_handler),
            base_url="http://unreachable.internal",
        )
        client = AsyncExternalHttpClient(
            base_url="http://unreachable.internal",
            max_retries=1,
            backoff_base=0.01,
            client=raw_httpx,
        )
        try:
            yield client
        finally:
            await raw_httpx.aclose()

    sample_app.dependency_overrides[get_external_client] = override_connect_error_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get("/api/v1/external/sample")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"
    assert body["error"]["correlation_id"] == response.headers["X-Correlation-ID"]


async def test_security_no_secret_leakage_in_logs_and_error_responses(caplog):
    """
    仮のトークン、Authorizationヘッダー、外部応答本文、スタックトレースが
    API応答とログのいずれにも現れないことを検証する。
    """
    caplog.set_level(logging.DEBUG, logger="common_platform")

    inbound_auth = "Bearer INBOUND_CLIENT_SECRET_TOKEN_111"
    outbound_auth = "Bearer OUTBOUND_BANK_API_SECRET_222"
    external_secret_body = "EXTERNAL_RAW_SECRET_PAYLOAD_333"

    def external_error_handler(request: httpx.Request) -> httpx.Response:
        # 外部APIが500エラーとともに機密情報を含む生ボディを返してきたケース
        trace_msg = f"Traceback (most recent call last): {external_secret_body}"
        return httpx.Response(
            500,
            text=f'{{"internal_trace": "{trace_msg}"}}',
        )

    async def override_secret_test_client() -> AsyncGenerator[AsyncExternalHttpClient]:
        raw_httpx = httpx.AsyncClient(
            transport=httpx.MockTransport(external_error_handler),
            base_url="http://secret-bank.internal",
        )
        client = AsyncExternalHttpClient(
            base_url="http://secret-bank.internal",
            max_retries=0,
            auth_adapter=StaticHeaderAuthAdapter("Authorization", outbound_auth),
            client=raw_httpx,
        )
        try:
            yield client
        finally:
            await raw_httpx.aclose()

    sample_app.dependency_overrides[get_external_client] = override_secret_test_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get(
            "/api/v1/external/sample",
            headers={"Authorization": inbound_auth},
        )

    # 1. API応答の検証（標準エラーのみが返り、機密値やスタックトレースが含まれないこと）
    assert response.status_code == 502
    response_text = response.text
    assert "INBOUND_CLIENT_SECRET_TOKEN_111" not in response_text
    assert "OUTBOUND_BANK_API_SECRET_222" not in response_text
    assert "EXTERNAL_RAW_SECRET_PAYLOAD_333" not in response_text
    assert "Traceback" not in response_text

    # 2. ログ出力の検証（1行JSON形式であり、機密値やスタックトレースが含まれないこと）
    raw_logs = _collect_all_logs(caplog)
    assert len(raw_logs.strip().splitlines()) >= 4
    for line in raw_logs.strip().splitlines():
        parsed = json.loads(line)
        assert "timestamp" in parsed
        assert "event" in parsed

    assert "INBOUND_CLIENT_SECRET_TOKEN_111" not in raw_logs
    assert "OUTBOUND_BANK_API_SECRET_222" not in raw_logs
    assert "EXTERNAL_RAW_SECRET_PAYLOAD_333" not in raw_logs
    assert "Traceback" not in raw_logs
