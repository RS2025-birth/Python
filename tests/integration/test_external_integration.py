import json
import logging
from collections.abc import AsyncGenerator

import httpx
import pytest
from apps.mock_api.main import app as mock_app
from apps.sample_api.main import app as sample_app
from apps.sample_api.main import get_external_client

from common_platform.auth import StaticHeaderAuthAdapter
from common_platform.http_client import AsyncExternalHttpClient
from common_platform.logger import JsonFormatter


@pytest.fixture(autouse=True)
async def setup_integrated_clients():
    """検証用APIの外部HTTPクライアントを、モックAPI(ASGI)へ直結するクライアントに差し替える。"""
    # テスト前にモックのカウンターを初期化
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app),
        base_url="http://mock-api.internal",
    ) as reset_client:
        await reset_client.post("/mock/reset")

    async def override_get_external_client() -> AsyncGenerator[AsyncExternalHttpClient]:
        raw_httpx = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_app),
            base_url="http://mock-api.internal",
        )
        client = AsyncExternalHttpClient(
            base_url="http://mock-api.internal",
            connect_timeout=2.0,
            read_timeout=2.0,
            max_retries=2,
            backoff_base=0.01,  # テスト高速化
            auth_adapter=StaticHeaderAuthAdapter(
                "Authorization", "Bearer integration-secret"
            ),
            client=raw_httpx,
        )
        try:
            yield client
        finally:
            await raw_httpx.aclose()

    sample_app.dependency_overrides[get_external_client] = override_get_external_client
    yield
    sample_app.dependency_overrides.clear()


def _get_logs(caplog) -> list[dict]:
    formatter = JsonFormatter()
    return [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.name == "common_platform"
    ]


async def test_integration_get_success_and_correlation_id_propagation(caplog):
    caplog.set_level(logging.INFO, logger="common_platform")
    custom_cid = "e2e-corr-id-12345"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get(
            "/api/v1/external/sample",
            headers={"X-Correlation-ID": custom_cid},
        )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == custom_cid
    body = response.json()
    assert body["status"] == "ok"
    assert body["external_response"]["data"]["sample_id"] == "SAMPLE-001"

    # 検証用API・HTTPクライアント・モックAPIの全ログで相関IDが一致していること
    logs = _get_logs(caplog)
    assert (
        len(logs) >= 6
    )  # sample開始 -> ext開始 -> mock開始 -> mock終了 -> ext終了 -> sample終了
    assert all(entry["correlation_id"] == custom_cid for entry in logs)

    # 認証トークンがログに一切漏れていないこと
    assert "integration-secret" not in json.dumps(logs)


async def test_integration_post_success_and_validation_error():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        # 正常POST
        res_ok = await ac.post("/api/v1/external/sample", json={"value": "hello-bank"})
        assert res_ok.status_code == 200
        assert res_ok.json()["external_response"]["received"] is True

        # 空白のみのPOST（検証用APIの入力エラー -> 400）
        res_empty = await ac.post("/api/v1/external/sample", json={"value": "   "})
        assert res_empty.status_code == 400
        assert res_empty.json()["error"]["code"] == "INVALID_INPUT"


async def test_integration_external_validation_error_mapped_to_502():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get(
            "/api/v1/external/sample",
            params={"scenario": "validation_error"},
        )

    # モックが400を返した場合、検証用APIは外部サービス異常（502）として標準エラーを返す
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert body["error"]["correlation_id"] == response.headers["X-Correlation-ID"]


async def test_integration_retry_then_success():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        # max_retries=2（計3回試行）なので、fail_until=2 なら3回目で成功する
        response = await ac.get(
            "/api/v1/external/sample",
            params={
                "scenario": "retry_then_success",
                "attempt_key": "e2e-retry-ok",
                "fail_until": 2,
            },
        )

    assert response.status_code == 200
    assert response.json()["external_response"]["status"] == "success"


async def test_integration_retry_exhausted_returns_503():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        # transient_error は常に503を返すため、上限到達後に503標準エラーを返す
        response = await ac.get(
            "/api/v1/external/sample",
            params={"scenario": "transient_error"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"
    assert body["error"]["correlation_id"] == response.headers["X-Correlation-ID"]


async def test_integration_invalid_json_from_external_returns_502():
    """外部APIが壊れたJSONを返した場合に502標準エラーへ変換されることを確認する。"""

    def broken_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{invalid-json-response")

    async def override_broken_client() -> AsyncGenerator[AsyncExternalHttpClient]:
        raw_httpx = httpx.AsyncClient(
            transport=httpx.MockTransport(broken_json_handler),
            base_url="http://broken-api.internal",
        )
        client = AsyncExternalHttpClient(
            base_url="http://broken-api.internal",
            client=raw_httpx,
        )
        try:
            yield client
        finally:
            await raw_httpx.aclose()

    sample_app.dependency_overrides[get_external_client] = override_broken_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sample_app),
        base_url="http://sample-api.internal",
    ) as ac:
        response = await ac.get("/api/v1/external/sample")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
