# Common API Platform & Verification Service (MVP)

## 1. システム概要

本リポジトリは、バックエンドAPI開発における非機能要件・共通処理を標準化するための**共通プラットフォーム基盤ライブラリ（`common_platform`）**、およびその動作を検証する**検証用API（`sample_api`）**と**外部APIモック（`mock_api`）**を提供します。

### 提供する共通機能
* **環境変数・設定管理**: Pydantic Settings による型安全な設定読み込みと起動時バリデーション（秘密値のマスク機能付き）。
* **相関ID（Correlation ID）伝播**: `X-Correlation-ID` の受信・UUIDv4自動生成・レスポンスヘッダー付与・外部APIへの自動引き継ぎ。
* **構造化JSONロギング**: 1行JSON形式での標準出力ログ（`request_started` / `request_completed` / `external_request_*` / `application_error`）および機密キーの自動マスク。
* **共通例外体系・標準エラー応答**: 業務・外部連携エラーの統一分類と、一貫したJSONエラーフォーマットへの自動変換。
* **共通非同期HTTPクライアント**: タイムアウト制御、認証ヘッダー付与、GET限定の指数バックオフ再試行、POST非再試行の標準化。

### 対象利用者
* バックエンドAPIを新規開発するアプリケーション開発者
* 障害調査・ログ監査を行うシステム運用担当者

### 対象外範囲（MVPスコープ外）
* **Web画面（UI / フロントエンド）の提供は行いません**（REST APIおよびJSON応答のみを提供します）。
* 永続化データベース（RDBMS/NoSQL）への業務データ保存。
* 本番向け認証基盤（OAuth2 / OIDCサーバー等）自体の構築およびコンテナレジストリへの自動公開。

---

## 2. ローカル起動手順

### A. Python仮想環境（.venv）での起動とテスト

1. **仮想環境の作成と有効化**
   ```powershell
   # Windows PowerShell
   python -m venv .venv
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
   .\.venv\Scripts\Activate.ps1
   ```
   ```bash
   # macOS / Linux
   python -m venv .venv
   source .venv/bin/activate
   ```

2. **依存関係のインストール**
   ```bash
   pip install -e ".[dev]"
   ```

3. **環境変数の準備**
   ```bash
   cp .env.example .env
   # 必要に応じて .env 内の接続先URLなどを編集してください（実トークンはGitへコミットしないこと）
   ```

4. **アプリケーションの起動（2つのターミナルでそれぞれ実行）**
   ```bash
   # ターミナル1: 外部APIモック起動 (ポート8001)
   uvicorn apps.mock_api.main:app --reload --port 8001

   # ターミナル2: 検証用API起動 (ポート8000)
   uvicorn apps.sample_api.main:app --reload --port 8000
   ```
   * 停止する場合は各ターミナルで `Ctrl + C` を押します。
   * 開発用APIドキュメント（Swagger UI）は `http://localhost:8000/docs` から確認できます。

### B. Docker Compose での起動と停止

1. **ビルドおよびバックグラウンド起動**
   ```bash
   docker compose up --build -d
   ```

2. **稼働・ヘルスチェック状態の確認**
   ```bash
   docker compose ps
   ```
   `bank-mock-api` と `bank-sample-api` の両方が `healthy` であることを確認します。

3. **ログのリアルタイム確認**
   ```bash
   docker compose logs -f
   ```

4. **コンテナの停止と削除**
   ```bash
   docker compose down
   ```

---

## 3. 環境変数一覧

すべての設定項目はプレフィックス `APP_` を付与して環境変数または `.env` から注入します。**実際の認証トークンやAPIキーは `.env.example` やソースコードに絶対に記載しないでください。**

| 環境変数名 | 型 | 必須/任意 | 用途 | 初期値 | 秘密情報 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `APP_LOG_LEVEL` | `Literal` | 任意 | ログ出力レベル（`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`） | `INFO` | いいえ |
| `APP_EXTERNAL_API_BASE_URL` | `HttpUrl` | **必須** | 外部APIのベースURL（HTTP/HTTPS形式） | なし | いいえ |
| `APP_CONNECT_TIMEOUT` | `float` | 任意 | 外部APIへの接続タイムアウト秒数（0より大きい値） | `5.0` | いいえ |
| `APP_READ_TIMEOUT` | `float` | 任意 | 外部APIからの読取タイムアウト秒数（0より大きい値） | `30.0` | いいえ |
| `APP_MAX_RETRIES` | `int` | 任意 | GET失敗時の最大再試行回数（0以上の整数） | `3` | いいえ |
| `APP_AUTH_HEADER_NAME` | `str` | 任意 | 外部APIへ付与する認証HTTPヘッダー名 | `Authorization` | いいえ |
| `APP_AUTH_HEADER_VALUE` | `SecretStr` | **必須** | 外部APIへ付与する認証HTTPヘッダー値 | なし | **はい** |

---

## 4. API仕様と利用例

### 検証用API（`sample_api`: ポート8000）

#### ① ヘルスチェック: `GET /health`
稼働状態を返します。内部構成や秘密情報は含みません。
* **応答例（200 OK）**:
  ```json
  {
    "status": "ok"
  }
  ```

#### ② 外部連携GET検証: `GET /api/v1/external/sample`
共通HTTPクライアント経由でモックAPIのGETを呼び出します。
* **クエリパラメータ**:
  * `scenario`（任意）: 外部モックの挙動指定（`success`, `validation_error`, `transient_error`, `retry_then_success`）
  * `attempt_key`（任意）: `retry_then_success` 時の試行カウント識別キー
  * `fail_until`（任意）: `retry_then_success` 時に失敗させる回数（既定: `2`）
* **利用例**:
  ```bash
  curl -i -H "X-Correlation-ID: req-sample-001" "http://localhost:8000/api/v1/external/sample"
  ```
* **標準成功応答（200 OK）**:
  ```json
  {
    "status": "ok",
    "external_response": {
      "status": "success",
      "source": "mock_external_api",
      "data": {
        "sample_id": "SAMPLE-001",
        "message": "架空の外部API取得成功レスポンスです。"
      }
    }
  }
  ```

#### ③ 外部連携POST検証: `POST /api/v1/external/sample`
リクエストボディの `value` を必須文字列として検証し、モックAPIへ送信します（失敗時も自動再試行しません）。
* **リクエストボディ**: `{"value": "任意の文字列"}`（空文字や空白のみは `400 Bad Request`）
* **利用例**:
  ```bash
  curl -i -X POST "http://localhost:8000/api/v1/external/sample" \
    -H "Content-Type: application/json" \
    -d '{"value": "hello-platform"}'
  ```

### 外部APIモック（`mock_api`: ポート8001）
* `GET /mock/external/sample` / `POST /mock/external/sample`: `scenario` に応じた応答を返却（未定義シナリオは `400`）。
* `POST /mock/reset`: `retry_then_success` 用の呼び出し回数カウンターを初期化。

### 標準エラー応答フォーマット
すべての業務エラー・バリデーションエラー・外部連携エラー・未捕捉例外は以下の統一形式で返却されます。

```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "入力内容に不備があります。",
    "correlation_id": "4d88f3df-1af1-4fcb-8586-7557ad981849"
  }
}
```

| エラーコード | HTTPステータス | 発生条件 |
| :--- | :--- | :--- |
| `INVALID_INPUT` | `400` | リクエストパラメータやJSONボディの入力不備 |
| `AUTHENTICATION_FAILED` | `401` | 認証失敗（外部APIからの401/403応答を含む） |
| `EXTERNAL_SERVICE_ERROR` | `502` | 外部APIの4xx/500応答、または不正なJSON応答 |
| `EXTERNAL_SERVICE_UNAVAILABLE` | `503` | 外部APIの接続失敗、タイムアウト、または502/503/504の再試行上限到達 |
| `INTERNAL_ERROR` | `500` | アプリケーション内部の未捕捉例外 |

---

## 5. 詳細技術仕様・運用手順

共通ライブラリの公開API仕様および障害発生時のログ調査手順については、以下を参照してください。
* [技術仕様書・運用手順書 (`docs/technical_specification.md`)](docs/technical_specification.md)


