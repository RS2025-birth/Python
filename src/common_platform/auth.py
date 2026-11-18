from typing import Protocol

from pydantic import SecretStr


class AuthAdapter(Protocol):
    """外部API向け認証ヘッダー生成インターフェース。"""

    def get_headers(self) -> dict[str, str]:
        """付与すべき認証ヘッダーの辞書を返す。"""
        ...


class StaticHeaderAuthAdapter:
    """固定トークンまたはAPIキーを指定ヘッダーへ付与するアダプター。"""

    def __init__(self, header_name: str, header_value: str | SecretStr) -> None:
        self._header_name = header_name
        if isinstance(header_value, SecretStr):
            self._header_value = header_value
        else:
            self._header_value = SecretStr(header_value)

    def get_headers(self) -> dict[str, str]:
        value = self._header_value.get_secret_value()
        if not self._header_name or not value:
            return {}
        return {self._header_name: value}

    def __repr__(self) -> str:
        return (
            f"StaticHeaderAuthAdapter("
            f"header_name={self._header_name!r}, header_value='***')"
        )
