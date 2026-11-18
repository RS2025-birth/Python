import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from common_platform.correlation import set_correlation_id
from common_platform.logger import get_logger, log_event

logger = get_logger()


class CorrelationAndLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 1. 相関IDの取得・生成とコンテキスト保存
        incoming_id = request.headers.get("X-Correlation-ID")
        correlation_id = set_correlation_id(incoming_id)

        method = request.method
        path = request.url.path

        # 2. リクエスト開始ログ
        log_event(
            logger,
            logging.INFO,
            "request_started",
            method=method,
            path=path,
        )

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            log_event(
                logger,
                logging.ERROR,
                "request_failed",
                method=method,
                path=path,
                status_code=500,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise

        # 3. 処理時間計測と終了ログ
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "request_completed",
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        # 4. すべての応答ヘッダーにX-Correlation-IDを設定
        response.headers["X-Correlation-ID"] = correlation_id
        return response
