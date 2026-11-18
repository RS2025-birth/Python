from pydantic import BaseModel


# --- 標準エラー応答モデル ---
class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None


class StandardErrorResponse(BaseModel):
    error: ErrorDetail


# --- 共通例外クラス ---
class PlatformError(Exception):
    """プラットフォーム共通の基底例外クラス。"""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    default_message: str = "内部エラーが発生しました。"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class InvalidInputError(PlatformError):
    """入力エラー（400 Bad Request）"""

    status_code = 400
    error_code = "INVALID_INPUT"
    default_message = "入力内容に不備があります。"


class AuthenticationError(PlatformError):
    """認証失敗（401 Unauthorized）"""

    status_code = 401
    error_code = "AUTHENTICATION_FAILED"
    default_message = "認証に失敗しました。"


class ExternalServiceError(PlatformError):
    """外部サービス応答異常（502 Bad Gateway）"""

    status_code = 502
    error_code = "EXTERNAL_SERVICE_ERROR"
    default_message = "外部サービスでエラーが発生しました。"


class ExternalServiceUnavailableError(PlatformError):
    """外部サービス利用不可・接続失敗（503 Service Unavailable）"""

    status_code = 503
    error_code = "EXTERNAL_SERVICE_UNAVAILABLE"
    default_message = "外部サービスに接続できないか、現在利用できません。"


class InternalServerError(PlatformError):
    """内部エラー（500 Internal Server Error）"""

    status_code = 500
    error_code = "INTERNAL_ERROR"
    default_message = "内部エラーが発生しました。"
