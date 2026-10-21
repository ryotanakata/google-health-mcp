from __future__ import annotations

from urllib.parse import urlparse

from dotenv import load_dotenv
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from src.claude_auth import ClaudeOAuthProvider
from src.config import Settings
from src.constants import CLAUDE_ORIGIN, MCP_PATH, OAUTH_LOGIN_PATH, OAUTH_SCOPE
from src.google_auth import GoogleAuthManager
from src.models import (
    ActivityTrend,
    BodyMeasurements,
    DailyActivity,
    ExerciseHistory,
    HealthMetrics,
    HeartRateSummary,
    SleepLog,
)
from src.repository import HealthRepository
from src.service import HealthService


def create_app(settings: Settings) -> Starlette:
    health_service = HealthService(HealthRepository(GoogleAuthManager(settings)))
    claude_oauth_provider = ClaudeOAuthProvider(
        settings.mcp_shared_secret, settings.mcp_token_signing_key, settings.public_base_url
    )

    mcp = MCPServer(
        "google-health",
        auth_server_provider=claude_oauth_provider,
        auth=AuthSettings(
            issuer_url=settings.public_base_url,
            resource_server_url=f"{settings.public_base_url}{MCP_PATH}",
            required_scopes=[OAUTH_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[OAUTH_SCOPE], default_scopes=[OAUTH_SCOPE]
            ),
            # トークンは自前の署名鍵でしか発行されず、この MCP サーバー専用のため
            # resource の照合は不要
            validate_token_resource=False,
        ),
    )

    @mcp.tool()
    async def get_daily_activity(date: str) -> DailyActivity:
        """指定日（YYYY-MM-DD）の歩数・消費カロリー・移動距離・アクティブ時間・上った階数と、
        アクティブゾーン時間（fat_burn / cardio / peak ごとの値とその合計）を取得する。
        アクティブゾーン時間は中強度以上の運動で獲得する指標で、cardio と peak は1分あたり2が
        加算される。1日の全時間をゾーンに振り分けた滞在時間が必要なときは
        get_heart_rate_summary を使う。"""
        return await health_service.get_daily_activity(date)

    @mcp.tool()
    async def get_sleep_log(date: str) -> SleepLog:
        """指定日（YYYY-MM-DD）の朝に終わった睡眠のログ（総睡眠時間・効率・ステージ内訳）を取得する。"""
        return await health_service.get_sleep_log(date)

    @mcp.tool()
    async def get_heart_rate_summary(date: str) -> HeartRateSummary:
        """指定日（YYYY-MM-DD）の安静時心拍数と、心拍が上がっていた時間（moderate / vigorous /
        peak ゾーンの滞在分とその合計）を取得する。"""
        return await health_service.get_heart_rate_summary(date)

    @mcp.tool()
    async def get_exercise_history(start_date: str, end_date: str) -> ExerciseHistory:
        """期間内（YYYY-MM-DD〜YYYY-MM-DD、両端を含む・最大366日）のワークアウトセッション履歴を取得する。"""
        return await health_service.get_exercise_history(start_date, end_date)

    @mcp.tool()
    async def get_health_metrics(date: str) -> HealthMetrics:
        """指定日（YYYY-MM-DD）の体調の指標を取得する。心拍変動（HRV）・呼吸数・血中酸素
        ウェルネス（SpO2）・皮膚温の基準値との差は、主に睡眠中に計測される。VO2 Max（心肺
        フィットネスレベル）も返す。計測されていない指標は null になる。"""
        return await health_service.get_health_metrics(date)

    @mcp.tool()
    async def get_body_measurements(start_date: str, end_date: str) -> BodyMeasurements:
        """期間内（YYYY-MM-DD〜YYYY-MM-DD、両端を含む・最大90日）の体重（kg）・体脂肪率（%）を
        取得する。期間の最初・最後・最小・最大・平均・記録のあった日数と、記録があった日ごとの
        値（その日の最後の記録）を返す。"""
        return await health_service.get_body_measurements(start_date, end_date)

    @mcp.tool()
    async def get_activity_trend(start_date: str, end_date: str) -> ActivityTrend:
        """期間内（YYYY-MM-DD〜YYYY-MM-DD、両端を含む・最大90日）の日別の歩数・移動距離・
        消費カロリー・アクティブ時間・上った階数・アクティブゾーン時間・安静時心拍数と、期間の
        合計・平均（データのない日を除く）を取得する。1日分だけなら get_daily_activity を使う。"""
        return await health_service.get_activity_trend(start_date, end_date)

    mcp.custom_route(OAUTH_LOGIN_PATH, methods=["GET", "POST"])(
        claude_oauth_provider.handle_login
    )

    # Cloud Run の公開ホスト名を明示的に許可する。未指定だと SDK は localhost 以外を 421 で拒否する
    public_host = urlparse(settings.public_base_url).netloc
    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        # セッションを持たず1リクエストで完結させる。Cloud Run の複数インスタンス・
        # スケールゼロに強く、接続を張りっぱなしにしないので CPU 課金も抑えられる
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host],
            allowed_origins=[settings.public_base_url, CLAUDE_ORIGIN],
        ),
    )


if __name__ == "__main__":
    import uvicorn

    load_dotenv()
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)
