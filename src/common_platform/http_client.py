from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from common_platform.auth import AuthAdapter, StaticHeaderAuthAdapter
from common_platform.config import AppSettings
from common_platform.correlation import get_correlation_id
from common_platform.exceptions import (
    AuthenticationError,
    ExternalServiceError,
    ExternalServiceUnavailableError,
)
from common_platform.logger import get_logger, log_event

logger = get_logger()

RETRYABLE_STATUS_CODES = {502, 503, 504}


def _sanitize_url_path(base_url: str, path: str) -> str:
    """クエリ文字列や認証情報を除外した安全なエンドポイント識別文字列を返す。"""
    joined = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    parts = urlsplit(joined)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class AsyncExternalHttpClient:
    """外部REST APIとのJSON通信を行う共通非同期HTTPクライアント。"""

    def __init__(
        self,
        base_url: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        auth_adapter: AuthAdapter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.auth_adapter = auth_adapter

        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._external_client = client
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        )

    async def close(self) -> None:
        if self._external_client is None:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncExternalHttpClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _build_headers(
        self, extra_headers: dict[str, str] | None = None
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        cid = get_correlation_id()
        if cid:
            headers["X-Correlation-ID"] = cid

        if self.auth_adapter:
            headers.update(self.auth_adapter.get_headers())

        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _raise_mapped_http_error(self, status_code: int) -> None:
        if status_code in (401, 403):
            raise AuthenticationError("外部サービスの認証に失敗しました。")
        if status_code in RETRYABLE_STATUS_CODES:
            raise ExternalServiceUnavailableError(
                f"外部サービスが一時的に利用できません (HTTP {status_code})。"
            )
        raise ExternalServiceError(
            f"外部サービスがエラー応答を返しました (HTTP {status_code})。"
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        is_get = method.upper() == "GET"
        allowed_retries = self.max_retries if is_get else 0
        safe_target = _sanitize_url_path(self.base_url, path)
        req_headers = self._build_headers(headers)

        attempt = 0
        while True:
            start_time = time.perf_counter()
            log_event(
                logger,
                logging.INFO,
                "external_request_started",
                method=method.upper(),
                target=safe_target,
                attempt=attempt + 1,
            )

            try:
                response = await self._client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=json_body,
                    headers=req_headers,
                )
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.PoolTimeout,
            ) as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                log_event(
                    logger,
                    logging.WARNING if attempt < allowed_retries else logging.ERROR,
                    "external_request_failed",
                    method=method.upper(),
                    target=safe_target,
                    attempt=attempt + 1,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                )
                if attempt < allowed_retries:
                    await asyncio.sleep(self.backoff_base * (2**attempt))
                    attempt += 1
                    continue
                raise ExternalServiceUnavailableError(
                    "外部サービスへの接続または応答待機がタイムアウトしました。"
                ) from exc
            except httpx.RequestError as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                log_event(
                    logger,
                    logging.ERROR,
                    "external_request_failed",
                    method=method.upper(),
                    target=safe_target,
                    attempt=attempt + 1,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                )
                raise ExternalServiceError(
                    "外部サービスとの通信中にネットワークエラーが発生しました。"
                ) from exc

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            # 正常応答 (2xx)
            if 200 <= response.status_code < 300:
                log_event(
                    logger,
                    logging.INFO,
                    "external_request_completed",
                    method=method.upper(),
                    target=safe_target,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                if response.status_code == 204 or not response.content:
                    return None
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExternalServiceError(
                        "外部サービスの応答JSONの解析に失敗しました。"
                    ) from exc

            # エラー応答 (非2xx)
            log_event(
                logger,
                logging.WARNING
                if (
                    is_get
                    and response.status_code in RETRYABLE_STATUS_CODES
                    and attempt < allowed_retries
                )
                else logging.ERROR,
                "external_request_failed",
                method=method.upper(),
                target=safe_target,
                status_code=response.status_code,
                attempt=attempt + 1,
                duration_ms=duration_ms,
                error_type="HTTPStatusError",
            )

            # GETかつ再試行対象ステータス(502, 503, 504)の場合のみ再試行
            if (
                is_get
                and response.status_code in RETRYABLE_STATUS_CODES
                and attempt < allowed_retries
            ):
                await asyncio.sleep(self.backoff_base * (2**attempt))
                attempt += 1
                continue

            self._raise_mapped_http_error(response.status_code)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request_json("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request_json(
            "POST", path, json_body=json_body, headers=headers
        )


def create_external_client_from_settings(
    settings: AppSettings,
) -> AsyncExternalHttpClient:
    """AppSettingsから共通非同期HTTPクライアントを生成する。"""
    auth = StaticHeaderAuthAdapter(
        header_name=settings.auth_header_name,
        header_value=settings.auth_header_value,
    )
    return AsyncExternalHttpClient(
        base_url=str(settings.external_api_base_url),
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
        max_retries=settings.max_retries,
        auth_adapter=auth,
    )
