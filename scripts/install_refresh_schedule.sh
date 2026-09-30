#!/usr/bin/env bash
# scripts/refresh_google_token.sh を macOS の launchd で毎日 REFRESH_HOUR 時に実行する。
# 更新が必要なのは約5日に1回で、それ以外の日は経過日数を確認して終わる。
# 実行時刻に Mac がスリープしていた場合は、復帰したときに実行される。
#
# 使い方:
#   scripts/install_refresh_schedule.sh              登録する（再実行すると上書き）
#   scripts/install_refresh_schedule.sh --uninstall  登録を解除する
#
# 環境変数（括弧内は省略時の値）:
#   PROJECT_ID (gcloud の既定プロジェクト) / REFRESH_HOUR (21)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.github.google-health-mcp.refresh-token
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/google-health-mcp-refresh.log"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST"
  echo "登録を解除しました"
  exit 0
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REFRESH_HOUR="${REFRESH_HOUR:-21}"
if [ -z "$PROJECT_ID" ]; then
  echo "PROJECT_ID が未設定です。環境変数か gcloud config set project で指定してください" >&2
  exit 1
fi

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
# launchd は最小限の PATH で起動するため、gcloud を見つけられるよう登録時の PATH を渡す
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_DIR/scripts/refresh_google_token.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$PATH</string>
    <key>PROJECT_ID</key>
    <string>$PROJECT_ID</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$REFRESH_HOUR</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF

launchctl bootstrap "$DOMAIN" "$PLIST"
echo "毎日 ${REFRESH_HOUR}:00 に refresh_token の期限を確認します（ログ: $LOG）"
