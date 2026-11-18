import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common_platform.exception_handlers import register_exception_handlers
from common_platform.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    ExternalServiceUnavailableError,
    InvalidInputError,
)
from common_platform.logger import JsonFormatter
from common_platform.middleware import CorrelationAndLoggingMiddleware


def _create_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationAndLoggingMiddleware)
    register_exception_handlers(app)

    @app.get("/invalid-input")
    async def raise_invalid():
        raise InvalidInputError("パラメータが不正です。")

    @app.get("/validation-error")
    async def query_validation(count: int):
        return {"count": count}

    @app.get("/auth-error")
    async def raise_auth():
        raise AuthenticationError()

    @app.get("/external-unavailable")
    async def raise_unavailable():
        raise ExternalServiceUnavailableError()

    @app.get("/external-error")
    async def raise_external():
        raise ExternalServiceError()

    @app.get("/unhandled-error")
    async def raise_unhandled():
        # 例外メッセージに秘密値が含まれていてもログに出ないことを検証する
        raise RuntimeError("DB_PASSWORD=super_secret_password_123")

    return app


def _extract_app_error_logs(caplog) -> list[dict[str, Any]]:
    formatter = JsonFormatter()
    logs = []
    for record in caplog.records:
        if record.name == "common_platform":
            parsed = json.loads(formatter.format(record))
            if parsed.get("event") == "application_error":
                logs.append(parsed)
    return logs


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code", "expected_type"),
    [
        ("/invalid-input", 400, "INVALID_INPUT", "InvalidInputError"),
        ("/validation-error?count=abc", 400, "INVALID_INPUT", "RequestValidationError"),
        ("/auth-error", 401, "AUTHENTICATION_FAILED", "AuthenticationError"),
        (
            "/external-unavailable",
            503,
            "EXTERNAL_SERVICE_UNAVAILABLE",
            "ExternalServiceUnavailableError",
        ),
        ("/external-error", 502, "EXTERNAL_SERVICE_ERROR", "ExternalServiceError"),
        ("/unhandled-error", 500, "INTERNAL_ERROR", "RuntimeError"),
    ],
)
def test_exception_handling_and_logging(
    caplog, path: str, expected_status: int, expected_code: str, expected_type: str
):
    caplog.set_level(logging.INFO, logger="common_platform")
    client = TestClient(_create_test_app(), raise_server_exceptions=False)

    test_cid = "test-corr-id-98765"
    response = client.get(path, headers={"X-Correlation-ID": test_cid})

    # 1. HTTPステータスと標準エラー応答の検証
    assert response.status_code == expected_status
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == expected_code
    assert body["error"]["correlation_id"] == test_cid
    assert (
        isinstance(body["error"]["message"], str) and len(body["error"]["message"]) > 0
    )

    # 2. application_error ログの検証
    error_logs = _extract_app_error_logs(caplog)
    assert len(error_logs) == 1
    log_entry = error_logs[0]
    assert log_entry["correlation_id"] == test_cid
    assert log_entry["error_code"] == expected_code
    assert log_entry["error_type"] == expected_type

    # 3. スタックトレースや例外内の秘密値が含まれていないことの検証
    raw_log_str = json.dumps(log_entry)
    assert "Traceback" not in raw_log_str
    assert "super_secret_password_123" not in raw_log_str
