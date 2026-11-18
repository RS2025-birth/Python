import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field

from common_platform.config import AppSettings, get_settings
from common_platform.exception_handlers import register_exception_handlers
from common_platform.exceptions import InvalidInputError
from common_platform.http_client import (
    AsyncExternalHttpClient,
    create_external_client_from_settings,
)
from common_platform.middleware import CorrelationAndLoggingMiddleware


class HealthResponse(BaseModel):
    """ヘルスチェックのレスポンスモデル（秘密情報を含めない）。"""

    status: str


class SamplePostRequest(BaseModel):
    value: str = Field(..., description="外部APIへ送信する必須文字列")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """起動時に設定の検証とロギングの初期化を行う。"""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logging.getLogger(__name__).info("Application startup completed.")
    yield


app = FastAPI(
    title="Sample Verification API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(CorrelationAndLoggingMiddleware)
register_exception_handlers(app)


async def get_external_client(
    settings: Annotated[AppSettings, Depends(get_settings)],
) -> AsyncGenerator[AsyncExternalHttpClient]:
    """設定から共通非同期HTTPクライアントを生成し、リクエスト後にクローズする。"""
    client = create_external_client_from_settings(settings)
    try:
        yield client
    finally:
        await client.close()


ExternalClientDep = Annotated[AsyncExternalHttpClient, Depends(get_external_client)]


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """稼働状態を返すヘルスチェックエンドポイント。"""
    return HealthResponse(status="ok")


@app.get("/api/v1/external/sample")
async def proxy_get_external_sample(
    client: ExternalClientDep,
    scenario: str = Query(default="success", description="外部モックの動作シナリオ"),
    attempt_key: str = Query(default="default", description="再試行カウント識別キー"),
    fail_until: int = Query(
        default=2, ge=0, description="retry_then_success時の失敗回数"
    ),
) -> dict[str, Any]:
    params = {
        "scenario": scenario,
        "attempt_key": attempt_key,
        "fail_until": fail_until,
    }
    external_data = await client.get("/mock/external/sample", params=params)
    return {
        "status": "ok",
        "external_response": external_data,
    }


@app.post("/api/v1/external/sample")
async def proxy_post_external_sample(
    payload: SamplePostRequest,
    client: ExternalClientDep,
    scenario: str = Query(default="success", description="外部モックの動作シナリオ"),
    attempt_key: str = Query(default="default", description="再試行カウント識別キー"),
    fail_until: int = Query(
        default=2, ge=0, description="retry_then_success時の失敗回数"
    ),
) -> dict[str, Any]:
    if not payload.value or not payload.value.strip():
        raise InvalidInputError("valueは必須文字列です。空白のみは許可されていません。")

    # クエリパラメータを付与して外部モックAPIへPOST送信
    path = (
        f"/mock/external/sample?scenario={scenario}"
        f"&attempt_key={attempt_key}&fail_until={fail_until}"
    )
    external_data = await client.post(path, json_body={"value": payload.value})
    return {
        "status": "ok",
        "external_response": external_data,
    }
