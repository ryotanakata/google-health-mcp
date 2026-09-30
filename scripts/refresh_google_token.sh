#!/usr/bin/env bash
# 同意画面の公開ステータスが「テスト」の OAuth クライアントでは、Google の refresh_token は
# 発行から7日で失効する。Secret Manager の最新版が REFRESH_AFTER_DAYS 日以上前のものなら、
# ブラウザで再認可して新しいトークンを Secret Manager と Cloud Run に反映する。
# ブラウザで「許可」を押す操作だけは人が行う必要がある。
#
# 使い方:
#   scripts/refresh_google_token.sh          古くなっていれば更新する
#   scripts/refresh_google_token.sh --force  経過日数に関係なく更新する
#
# 環境変数（括弧内は省略時の値）:
#   PROJECT_ID (gcloud の既定プロジェクト) / REGION (asia-northeast1)
#   SERVICE (google-health-mcp) / REFRESH_AFTER_DAYS (5)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO_DIR/.venv/bin/python"
SECRET=GOOGLE_REFRESH_TOKEN
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-google-health-mcp}"
REFRESH_AFTER_DAYS="${REFRESH_AFTER_DAYS:-5}"

log() {
  echo "$(date '+%F %T') $1"
}

notify() {
  log "$1"
  if command -v osascript >/dev/null; then
    osascript -e "display notification \"$1\" with title \"google-health-mcp\"" || true
  fi
}

trap 'notify "refresh_token の更新に失敗しました。ログを確認してください"' ERR

if [ -z "$PROJECT_ID" ]; then
  log "PROJECT_ID が未設定です。環境変数か gcloud config set project で指定してください"
  exit 1
fi

if [ "${1:-}" != "--force" ]; then
  created=$(gcloud secrets versions describe latest --secret="$SECRET" \
    --project="$PROJECT_ID" --format='value(createTime)')
  age_days=$("$PYTHON" -c '
import datetime as dt, sys
created = dt.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print((dt.datetime.now(dt.timezone.utc) - created).days)
' "$created")
  if [ "$age_days" -lt "$REFRESH_AFTER_DAYS" ]; then
    log "最新の refresh_token は発行から ${age_days} 日。更新は不要です"
    exit 0
  fi
fi

notify "Google の再認可が必要です。開いたブラウザで「許可」を押してください"
token=$("$PYTHON" "$REPO_DIR/auth_setup.py" \
  --client-secret "$REPO_DIR/client_secret.json" --token-only)
if [ -z "$token" ]; then
  notify "refresh_token を取得できませんでした"
  exit 1
fi

new_version=$(printf '%s' "$token" | gcloud secrets versions add "$SECRET" \
  --project="$PROJECT_ID" --data-file=- --format='value(name)')
unset token

# シークレットはインスタンスの起動時に読まれるため、新しいリビジョンを作って反映する
gcloud run services update "$SERVICE" --project="$PROJECT_ID" --region="$REGION" \
  --update-secrets="$SECRET=$SECRET:latest" --quiet

# 失効済みの古い版を残すと、有効なバージョン数に応じた Secret Manager の課金が積み上がる
for version in $(gcloud secrets versions list "$SECRET" --project="$PROJECT_ID" \
  --filter='state=ENABLED' --format='value(name)'); do
  if [ "${version##*/}" != "${new_version##*/}" ]; then
    gcloud secrets versions destroy "${version##*/}" --secret="$SECRET" \
      --project="$PROJECT_ID" --quiet
  fi
done

notify "refresh_token を更新しました（Secret Manager 版 ${new_version##*/}）"
