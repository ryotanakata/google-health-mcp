from __future__ import annotations

from mcp.types import ToolAnnotations

# --- 環境変数の検証（config.py） ---

# トークン署名鍵の最短長。署名済みの client_id は誰でも登録で入手でき、鍵をオフラインで
# 総当たりする材料になるため、推測できない長さを強制する（openssl rand -hex 32 で64文字）
OAUTH_SIGNING_KEY_MIN_LENGTH = 32

# --- MCP サーバー（main.py） ---

MCP_PATH = "/mcp"
# ツールが読み取りだけか、書き込み・削除を伴うかを Claude に伝える注釈（mcp-tools.md）
MCP_READ_ONLY_TOOL = ToolAnnotations(read_only_hint=True)
MCP_WRITE_TOOL = ToolAnnotations(read_only_hint=False)
MCP_DESTRUCTIVE_TOOL = ToolAnnotations(read_only_hint=False, destructive_hint=True)
# Claude.ai はブラウザ経由ではなくサーバー側から接続するが、Origin を付けてきた場合に備えて許可する
CLAUDE_ORIGIN = "https://claude.ai"

# --- Google 側の認証（google_auth.py・auth_setup.py） ---

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# スコープ名は googlehealth. で始まる（health.steps.readonly のような名前は存在しない）。
# 書き込みは食事・水分の nutrition.writeonly のみ（architecture.md の不変条件）。
# nutrition.readonly は公式のデータ型一覧にはあるがディスカバリドキュメントのスコープ一覧には
# 無く、同意画面で付けられないことがある。付かない場合に読めるのは、このサーバーが書いた記録だけ
GOOGLE_HEALTH_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.writeonly",
    "https://www.googleapis.com/auth/googlehealth.settings.readonly",
]
# auth_setup.py --token-only で、ブラウザでの許可を待つ上限
GOOGLE_REAUTH_TIMEOUT_SECONDS = 10 * 60

# --- ツール入力の検証と要約（service.py） ---

# get_exercise_history の期間上限（両端を含む日数）。QUERY_MAX_DAYS ごとの分割は直列に
# 送るため、期間が長いとリクエスト数に比例して応答が遅れ、API の利用枠も消費する
EXERCISE_HISTORY_MAX_DAYS = 366
# time-in-heart-rate-zone の日次集計は1日（1440分）を必ずどれかのゾーンに振り分け、LIGHT には
# 睡眠・安静・未装着を含む残りの時間が入る。運動時間と誤読されないよう、要約はこの3ゾーンに限る
HEART_RATE_ELEVATED_ZONES = ("MODERATE", "VIGOROUS", "PEAK")
# get_body_measurements の期間上限（両端を含む日数）。1リクエストの上限（QUERY_MAX_DAYS）と
# 同じ値にして、期間の分割なしで取れる範囲に収める
BODY_MEASUREMENTS_MAX_DAYS = 90
# get_activity_trend の期間上限（両端を含む日数）。14日上限のデータ型はこの期間で7分割されるため、
# これより長くするとリクエスト数に比例して応答が遅れる
ACTIVITY_TREND_MAX_DAYS = 90

# --- 食事・水分の記録（service.py） ---

# 記録の現地時刻を解釈するタイムゾーン。API の interval は UTC と UTC オフセットを要求するため、
# date・time の省略時の現在時刻と、指定された日時のオフセットをこのタイムゾーンで決める
NUTRITION_LOG_TIME_ZONE = "Asia/Tokyo"
# API は interval に startTime < endTime を要求する。食事・水分は時点の記録なので最小の幅を置く
NUTRITION_LOG_INTERVAL_MINUTES = 1
# log_meal で受け付ける NutritionLog.mealType。API の enum にはこのほかに BEFORE_BREAKFAST・
# ANYTIME などもあるが、Claude が選び分けられる4区分に絞る
NUTRITION_MEAL_TYPES = ("BREAKFAST", "LUNCH", "DINNER", "SNACK")
# 書き込み・削除を許可するデータ型（architecture.md の不変条件）
NUTRITION_WRITABLE_DATA_TYPES = ("nutrition-log", "hydration-log")

# --- Google Health API（repository.py） ---

# 利用者のリソースの起点。データポイント（/dataTypes/...）もデバイス（/pairedDevices）もこの下。
# 公式ディスカバリドキュメント: https://health.googleapis.com/$discovery/rest?version=v4
HEALTH_API_BASE_URL = "https://health.googleapis.com/v4/users/me"
# 1リクエストあたりのクエリ期間上限。
# heart-rate / active-minutes / total-calories / calories-in-heart-rate-zone は14日、それ以外は90日
QUERY_MAX_DAYS = 90
QUERY_SHORT_MAX_DAYS = 14
# exercise・sleep の list は pageSize の上限が25
SESSION_PAGE_SIZE = 25
HEALTH_API_MAX_ERROR_BODY_CHARS = 500
HEALTH_API_TIMEOUT_SECONDS = 30.0

# --- Claude 側の認証・OAuth 2.1 認可サーバー（claude_auth.py） ---

OAUTH_LOGIN_PATH = "/oauth/login"
OAUTH_SCOPE = "health"

OAUTH_ACCESS_TOKEN_TTL_SECONDS = 60 * 60
OAUTH_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
OAUTH_AUTHORIZATION_CODE_TTL_SECONDS = 5 * 60
OAUTH_LOGIN_REQUEST_TTL_SECONDS = 10 * 60
# パスフレーズを間違えたときの待ち時間（総当たり対策）
OAUTH_FAILED_LOGIN_DELAY_SECONDS = 1.0

# Claude.ai / Desktop / mobile の固定コールバックと、
# Claude Code のループバック（ポート任意）のみ許可する
CLAUDE_CALLBACK_URL = "https://claude.ai/api/mcp/auth_callback"
OAUTH_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")
OAUTH_INVALID_REDIRECT_URI_MESSAGE = (
    f"redirect_uri は {CLAUDE_CALLBACK_URL} かループバックのみ登録できます"
)

# 利用者は本人1人のため、トークンの subject は固定値
OAUTH_TOKEN_SUBJECT = "owner"
OAUTH_JWT_ALGORITHM = "HS256"
# 同意画面のリクエストから認可コードへ引き継ぐ項目
OAUTH_LOGIN_CLAIMS = (
    "client_id",
    "redirect_uri",
    "redirect_uri_explicit",
    "code_challenge",
    "scopes",
    "state",
    "resource",
)
OAUTH_INVALID_REQUEST_MESSAGE = (
    "認可リクエストが無効か、期限切れです。Claude から接続し直してください。"
)
OAUTH_WRONG_PASSPHRASE_MESSAGE = "パスフレーズが違います。"
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
LOGIN_PAGE_HEADERS = {
    **NO_STORE_HEADERS,
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# --- 同意画面の HTML テンプレート（claude_auth.py） ---
# 差し込む値はここではエスケープされない。展開する claude_auth.py 側で必ず html.escape する

# ページ全体の枠。$body に各画面の本文が入る
LOGIN_PAGE_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google Health MCP</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 26rem;
         margin: 4rem auto; padding: 0 1rem; }
  input { width: 100%; padding: .5rem; margin: .5rem 0 1rem; box-sizing: border-box; }
  button { padding: .5rem 1.5rem; }
  .error { color: #b00020; }
</style>
</head>
<body>
<h1>Google Health MCP</h1>
$body
</body>
</html>"""

# パスフレーズの入力フォーム。$error_html は空文字か LOGIN_ERROR_TEMPLATE を展開したもの
LOGIN_FORM_TEMPLATE = """<p>Claude があなたの健康データの読み取りと、食事・水分の記録・削除への
アクセスを求めています。許可する場合はパスフレーズ（MCP_SHARED_SECRET）を入力してください。</p>
$error_html
<form method="post" action="$action">
  <input type="hidden" name="request" value="$login_request">
  <label>パスフレーズ
    <input type="password" name="passphrase" autocomplete="current-password" required autofocus>
  </label>
  <button type="submit">許可する</button>
</form>"""

LOGIN_ERROR_TEMPLATE = '<p class="error">$message</p>'
