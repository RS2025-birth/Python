import json
import logging
import uuid
from typing import Any

from apps.sample_api.main import app
from fastapi import Request
from fastapi.testclient import TestClient

from common_platform.logger import JsonFormatter

client = TestClient(app)


# テスト用のダミーPOSTエンドポイント（リクエスト本文を受け取るケースを検証）
@app.post("/_test_echo")
async def _test_echo(request: Request):
    _ = await request.body()
    return {"received": True, "external_secret": "SUPER_SECRET_EXTERNAL_DATA"}


def _extract_json_logs(caplog) -> list[dict[str, Any]]:
    """caplogで捕捉したレコードを実際のJsonFormatterで変換して取得する。"""
    formatter = JsonFormatter()
    logs = []
    for record in caplog.records:
        if record.name == "common_platform":
            logs.append(json.loads(formatter.format(record)))
    return logs


def test_correlation_id_generated_when_missing(caplog):
    caplog.set_level(logging.INFO, logger="common_platform")

    response = client.get("/health")
    assert response.status_code == 200

    # 応答ヘッダーにUUID形式のX-Correlation-IDが付与されていること
    cid = response.headers.get("X-Correlation-ID")
    assert cid is not None
    assert uuid.UUID(cid)

    # ログ出力の検証
    log_lines = _extract_json_logs(caplog)
    assert len(log_lines) == 2

    assert log_lines[0]["event"] == "request_started"
    assert log_lines[0]["correlation_id"] == cid
    assert log_lines[1]["event"] == "request_completed"
    assert log_lines[1]["status_code"] == 200
    assert isinstance(log_lines[1]["duration_ms"], float)


def test_correlation_id_propagated_and_invalid_rejected():
    # 有効なIDはそのまま採用される
    valid_id = "req-12345-abcde"
    res_valid = client.get("/health", headers={"X-Correlation-ID": valid_id})
    assert res_valid.headers["X-Correlation-ID"] == valid_id

    # 不正なID（記号や改行など）は破棄され新しいUUIDになる
    invalid_id = "invalid\nid;drop table"
    res_invalid = client.get("/health", headers={"X-Correlation-ID": invalid_id})
    new_id = res_invalid.headers["X-Correlation-ID"]
    assert new_id != invalid_id
    assert uuid.UUID(new_id)


def test_sensitive_data_not_logged(capsys):
    secret_token = "Bearer secret-token-99999"
    secret_body = {"password": "my_secret_password", "credit_card": "4111-2222"}

    response = client.post(
        "/_test_echo",
        headers={"Authorization": secret_token, "X-Api-Key": "secret-api-key"},
        json=secret_body,
    )
    assert response.status_code == 200

    captured = capsys.readouterr()
    raw_logs = captured.out

    # 認証ヘッダー値、リクエスト本文、外部応答本文が一切含まれないことを確認
    assert "secret-token-99999" not in raw_logs
    assert "secret-api-key" not in raw_logs
    assert "my_secret_password" not in raw_logs
    assert "4111-2222" not in raw_logs
    assert "SUPER_SECRET_EXTERNAL_DATA" not in raw_logs
