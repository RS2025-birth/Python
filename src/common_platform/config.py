from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """アプリケーションの環境別設定クラス。"""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="アプリケーションのログレベル",
    )
    external_api_base_url: HttpUrl = Field(
        ...,
        description="外部APIのベースURL（必須・HTTP/HTTPS形式）",
    )
    connect_timeout: float = Field(
        default=5.0,
        gt=0,
        description="外部APIへの接続タイムアウト秒数（0より大きい値）",
    )
    read_timeout: float = Field(
        default=30.0,
        gt=0,
        description="外部APIからの読取タイムアウト秒数（0より大きい値）",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        description="失敗時の最大再試行回数（0以上の整数）",
    )
    auth_header_name: str = Field(
        default="Authorization",
        min_length=1,
        description="認証に使用するHTTPヘッダー名",
    )
    auth_header_value: SecretStr = Field(
        ...,
        min_length=1,
        description="認証に使用するHTTPヘッダー値（必須・ログ出力時はマスクされる）",
    )


@lru_cache
def get_settings() -> AppSettings:
    """設定インスタンスをキャッシュして返す。"""
    return AppSettings()  # type: ignore[call-arg]
