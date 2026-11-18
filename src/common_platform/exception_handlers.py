import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common_platform.correlation import get_correlation_id
from common_platform.exceptions import (
    ErrorDetail,
    InvalidInputError,
    PlatformError,
    StandardErrorResponse,
)
from common_platform.logger import get_logger, log_event

logger = get_logger()


def _build_error_response(
    status_code: int,
    error_code: str,
    message: str,
    correlation_id: str | None,
) -> JSONResponse:
    body = StandardErrorResponse(
        error=ErrorDetail(
            code=error_code,
            message=message,
            correlation_id=correlation_id,
        )
    )
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """FastAPIアプリケーションへ共通例外ハンドラーを登録する。"""

    @app.exception_handler(PlatformError)
    async def handle_platform_error(
        request: Request, exc: PlatformError
    ) -> JSONResponse:
        cid = get_correlation_id()
        log_event(
            logger,
            logging.ERROR,
            "application_error",
            error_code=exc.error_code,
            error_type=type(exc).__name__,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return _build_error_response(exc.status_code, exc.error_code, exc.message, cid)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        cid = get_correlation_id()
        err = InvalidInputError()
        log_event(
            logger,
            logging.WARNING,
            "application_error",
            error_code=err.error_code,
            error_type=type(exc).__name__,
            status_code=err.status_code,
            path=request.url.path,
        )
        return _build_error_response(err.status_code, err.error_code, err.message, cid)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        cid = get_correlation_id()
        log_event(
            logger,
            logging.ERROR,
            "application_error",
            error_code="INTERNAL_ERROR",
            error_type=type(exc).__name__,
            status_code=500,
            path=request.url.path,
        )
        return _build_error_response(
            500,
            "INTERNAL_ERROR",
            "予期しない内部エラーが発生しました。",
            cid,
        )
