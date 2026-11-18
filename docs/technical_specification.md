# 共通プラットフォーム技術仕様書・運用手順書

## 1. 共通ライブラリ公開API仕様（`common_platform`）

### 1.1 設定管理（`common_platform.config`）
* **公開API**: `AppSettings`, `get_settings()`
* **利用方法**:
  ```python
  from common_platform.config import get_settings

  settings = get_settings()
  print(settings.external_api_base_url)
  ```
* **制約・注意事項**:
  * `get_settings()` は `@lru_cache` によりプロセス内で設定インスタンスをキャッシュします。
  * `auth_header_value` は `SecretStr` 型であり、`str(settings)` や `repr(settings)` では `'**********'` とマスクされます。生値が必要な場合は認証アダプター経由でのみ参照してください。

### 1.2 相関ID管理（`common_platform.correlation`）
* **公開API**: `get_correlation_id() -> str | None`, `set_correlation_id(correlation_id: str | None) -> str`
* **利用方法・制約**:
  * Python標準の `contextvars.ContextVar` を使用し、非同期タスク間で安全に相関IDを分離・保持します。
  * `CorrelationAndLoggingMiddleware` をFastAPIへ登録することで、リクエスト受信時に `X-Correlation-ID` を検証（英数字・ハイフン・アンダースコア、1〜128文字）し、未指定または不正な場合は自動でUUIDv4を採番します。

### 1.3 構造化JSONロガー（`common_platform.logger`）
* **公開API**: `get_logger(name, level)`, `log_event(logger, level, event, **fields)`
* **利用方法**:
  ```python
  import logging
  from common_platform.logger import get_logger, log_event

  logger = get_logger()
  log_event(logger, logging.INFO, "custom_step_completed", step="validation")
  ```
* **制約・注意事項**:
  * 1行のJSONオブジェクトとして標準出力（`stdout`）へ出力されます。
  * `authorization`, `api_key`, `token`, `secret`, `password`, `body`, `payload` などの機密キーは自動的に `[REDACTED]` へ置換されます。
  * **リクエスト本文全体や外部APIの応答本文、認証トークンは絶対にログフィールドへ渡さないでください。**

### 1.4 共通例外・例外ハンドラー（`common_platform.exceptions` / `exception_handlers`）
* **公開API**:
  * 例外クラス: `PlatformError`, `InvalidInputError`, `AuthenticationError`, `ExternalServiceError`, `ExternalServiceUnavailableError`, `InternalServerError`
  * 登録関数: `register_exception_handlers(app)`
* **制約・注意事項**:
  * FastAPIアプリケーション初期化時に `register_exception_handlers(app)` を必ず呼び出してください。これにより、すべての共通例外と未捕捉例外が `StandardErrorResponse` 形式のJSONへ変換されます。

### 1.5 認証アダプター・共通非同期HTTPクライアント（`common_platform.auth` / `http_client`）
* **公開API**:
  * `AuthAdapter`（Protocol）, `StaticHeaderAuthAdapter(header_name, header_value)`
  * `AsyncExternalHttpClient`, `create_external_client_from_settings(settings)`
* **利用方法**:
  ```python
  async with create_external_client_from_settings(settings) as client:
      data = await client.get("/v1/resource", params={"type": "sample"})
  ```
* **制約・注意事項**:
  * 接続タイムアウトと読取タイムアウトが常に適用され、無制限待機は発生しません。
  * **GETリクエスト**のみ、ネットワーク接続障害・読取タイムアウト・HTTP `502 / 503 / 504` を対象に `max_retries` 回まで指数バックオフで自動再試行します。
  * **POSTリクエスト**は副作用（二重登録・二重決済等）を防ぐため、**いかなるエラーでも自動再試行しません**。

---

## 2. 運用・障害調査手順

### 2.1 相関IDを用いたリクエスト追跡手順
APIクライアントまたはエラー応答JSONの `error.correlation_id`（もしくはレスポンスヘッダーの `X-Correlation-ID`）を起点に、標準出力ログを検索します。

1. **Docker環境でのログ抽出例**:
   ```bash
   docker compose logs | grep "4d88f3df-1af1-4fcb-8586-7557ad981849"
   ```

2. **追跡する標準イベントの時系列**:
   * `request_started`: 検証用APIがリクエストを受信した時刻・メソッド・パスを確認。
   * `external_request_started` / `external_request_failed` / `external_request_completed`: 外部APIへの呼び出し先（クエリ除外済みパス）、試行回数（`attempt`）、応答ステータス、所要時間（`duration_ms`）を確認。
   * `application_error`: 変換された共通エラーコード（`error_code`）と発生した例外クラス名（`error_type`）を確認。
   * `request_completed`: 検証用APIがクライアントへ返却した最終HTTPステータスと総処理時間（`duration_ms`）を確認。

### 2.2 セキュリティ・ログ検索時の注意事項
* **秘密情報でのログ検索禁止**: APIキー、Authorizationトークン、顧客の個人情報やリクエスト本文の値を使ってログ基盤の検索クエリを実行しないでください（検索履歴自体に機密情報が残るのを防ぐため、必ず `correlation_id` または `path` / `error_code` で検索してください）。
* **スタックトレース非出力の仕様**: セキュリティ要件に基づき、`application_error` ログにはスタックトレースや例外の内部メッセージ詳細を出力していません。原因調査は `error_code`、`error_type`、および発生箇所の `path` と時刻から特定してください。
