"""初回のみ、ローカル環境でワンショット実行する。

ブラウザでGoogleの同意画面を開き、GOOGLE_REFRESH_TOKENを取得して表示する。
表示された値をSecret Managerに登録すること。

実行例:
    python auth_setup.py --client-secret client_secret.json
"""

from __future__ import annotations

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

from src.constants import GOOGLE_HEALTH_SCOPES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secret",
        default="client_secret.json",
        help="GCPコンソールでダウンロードしたOAuthクライアントシークレットのパス",
    )
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secret, GOOGLE_HEALTH_SCOPES
    )
    credentials = flow.run_local_server(port=0)

    print("\n取得した refresh_token を")
    print("Secret Manager の GOOGLE_REFRESH_TOKEN に登録してください:\n")
    print(credentials.refresh_token)


if __name__ == "__main__":
    main()
