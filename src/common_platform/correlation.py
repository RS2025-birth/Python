import re
import uuid
from contextvars import ContextVar

_correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# 英数字とハイフン・アンダースコアのみ許可（最大128文字）
_VALID_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_]{1,128}$")


def get_correlation_id() -> str | None:
    """現在のコンテキストから相関IDを取得する。"""
    return _correlation_id_ctx.get()


def set_correlation_id(correlation_id: str | None) -> str:
    """相関IDを検証し、不正または未指定ならUUIDv4を生成してコンテキストに保存する。"""
    if correlation_id and _VALID_ID_PATTERN.match(correlation_id):
        valid_id = correlation_id
    else:
        valid_id = str(uuid.uuid4())
    _correlation_id_ctx.set(valid_id)
    return valid_id
