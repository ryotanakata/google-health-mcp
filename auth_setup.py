"""ローカル環境で実行し、ブラウザでGoogleの同意画面を開いてGOOGLE_REFRESH_TOKENを取得する。

表示された値をSecret Managerに登録すること。--token-only はトークンだけを標準出力に書き、
scripts/refresh_google_token.sh がそのまま Secret Manager に渡す。

実行例:
    python auth_setup.py --client-secret client_secret.json
"""

from __future__ import annotations

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

from src.constants import GOOGLE_HEALTH_SCOPES, GOOGLE_REAUTH_TIMEOUT_SECONDS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secret",
        default="client_secret.json",
        help="GCPコンソールでダウンロードしたOAuthクライアントシークレットのパス",
    )
    parser.add_argument(
        "--token-only",
        action="store_true",
        help="refresh_tokenだけを標準出力に書く（scripts/refresh_google_token.sh用）",
    )
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secret, GOOGLE_HEALTH_SCOPES
    )
    if args.token_only:
        # 標準出力をトークンだけにするため案内文を出さない。定期実行で誰も許可しない場合に
        # 待ち続けないよう、期限を付ける
        credentials = flow.run_local_server(
            port=0,
            authorization_prompt_message="",
            timeout_seconds=GOOGLE_REAUTH_TIMEOUT_SECONDS,
        )
        print(credentials.refresh_token, end="")
        return

    credentials = flow.run_local_server(port=0)

    print("\n取得した refresh_token を")
    print("Secret Manager の GOOGLE_REFRESH_TOKEN に登録してください:\n")
    print(credentials.refresh_token)


if __name__ == "__main__":
    main()
