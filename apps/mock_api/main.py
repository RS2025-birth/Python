from collections import defaultdict
from typing import Any, Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from common_platform.exception_handlers import register_exception_handlers
from common_platform.exceptions import (
    ExternalServiceUnavailableError,
    InvalidInputError,
)
from common_platform.middleware import CorrelationAndLoggingMiddleware

app = FastAPI(title="External Mock API")
app.add_middleware(CorrelationAndLoggingMiddleware)
register_exception_handlers(app)

# 許可されたシナリオ一覧
ALLOWED_SCENARIOS = {
    "success",
    "validation_error",
    "transient_error",
    "retry_then_success",
}
ScenarioType = Literal[
    "success", "validation_error", "transient_error", "retry_then_success"
]

# 再試行シナリオ検証用の呼び出し回数カウンター
_call_counts: dict[str, int] = defaultdict(int)


class MockPostRequest(BaseModel):
    value: str = Field(..., description="送信テスト用の任意の値（空文字不可）")


def _validate_and_execute_scenario(
    scenario: str,
    attempt_key: str = "default",
    fail_until: int = 2,
) -> None:
    """シナリオ値を検証し、必要に応じて例外（400または503）を発生させる。"""
    if scenario not in ALLOWED_SCENARIOS:
        raise InvalidInputError(f"未定義のシナリオが指定されました: {scenario}")

    if scenario == "validation_error":
        raise InvalidInputError("外部モックAPIで入力エラーシナリオが指定されました。")

    if scenario == "transient_error":
        raise ExternalServiceUnavailableError(
            "外部モックAPIが一時障害（503）を返しました。"
        )

    if scenario == "retry_then_success":
        _call_counts[attempt_key] += 1
        if _call_counts[attempt_key] <= fail_until:
            count = _call_counts[attempt_key]
            raise ExternalServiceUnavailableError(
                f"外部モックAPIが一時障害中です ({count}/{fail_until}回目)。"
            )


@app.get("/health")
async def health_check() -> dict[str, Any]:
    return {"status": "ok", "service": "mock_api"}


@app.post("/mock/reset")
async def reset_mock_state() -> dict[str, Any]:
    """テスト間で呼び出し回数カウンターをリセットする。"""
    _call_counts.clear()
    return {"status": "reset_completed"}


@app.get("/mock/external/sample")
async def get_mock_sample(
    scenario: str = Query(default="success", description="実行シナリオ"),
    attempt_key: str = Query(default="default", description="再試行カウント識別キー"),
    fail_until: int = Query(
        default=2, ge=0, description="retry_then_success時に失敗させる回数"
    ),
) -> dict[str, Any]:
    _validate_and_execute_scenario(
        scenario, attempt_key=attempt_key, fail_until=fail_until
    )

    return {
        "status": "success",
        "source": "mock_external_api",
        "data": {
            "sample_id": "SAMPLE-001",
            "message": "架空の外部API取得成功レスポンスです。",
        },
    }


@app.post("/mock/external/sample")
async def post_mock_sample(
    payload: MockPostRequest,
    scenario: str = Query(default="success", description="実行シナリオ"),
    attempt_key: str = Query(default="default", description="再試行カウント識別キー"),
    fail_until: int = Query(
        default=2, ge=0, description="retry_then_success時に失敗させる回数"
    ),
) -> dict[str, Any]:
    # valueが空文字または空白のみの場合は標準的な入力エラーを返す
    if not payload.value or not payload.value.strip():
        raise InvalidInputError("valueは空にできません。")

    _validate_and_execute_scenario(
        scenario, attempt_key=attempt_key, fail_until=fail_until
    )

    # 受信したvalueは保存せず、受付結果のみを返す
    return {
        "status": "accepted",
        "source": "mock_external_api",
        "received": True,
    }
