# デプロイ

[English](deployment.md)

1. Google Cloud で Google Health API を有効化する。OAuth 同意画面（ユーザーの種類は**外部**）に `GOOGLE_HEALTH_SCOPES` の読み取り専用スコープ3つを追加し、公開ステータスは**「テスト」のまま**にして、自分の Google アカウントをテストユーザーに登録する。Google の審査や Web サイトは不要だが、refresh_token は7日で失効する（後述）
2. 種類が**「デスクトップアプリ」**の OAuth クライアントを作り、JSON をリポジトリ直下に `client_secret.json` として置く（`.gitignore` 済み）
3. `python auth_setup.py --client-secret client_secret.json` を実行し、4つの秘密情報を Secret Manager に登録する。値は入力待ちの画面に貼り付け、コマンド履歴に残さない（macOS 標準の zsh の書き方。bash では `read -rsp "クライアントシークレット: " V`）:
   ```bash
   printf '%s' "$(openssl rand -hex 24)" | gcloud secrets create MCP_SHARED_SECRET --data-file=-
   printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create MCP_TOKEN_SIGNING_KEY --data-file=-
   read -rs "V?クライアントシークレット: "; printf '%s' "$V" | gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-; unset V
   read -rs "V?refresh_token: "; printf '%s' "$V" | gcloud secrets create GOOGLE_REFRESH_TOKEN --data-file=-; unset V
   ```
   パスフレーズは `gcloud secrets versions access latest --secret=MCP_SHARED_SECRET` で取り出し、パスワードマネージャーに保存する。続けて `<プロジェクト番号>-compute@developer.gserviceaccount.com` に `roles/secretmanager.secretAccessor` を付与し、Cloud Run がシークレットを読めるようにする
4. 1回でデプロイする。Cloud Run の URL は `https://<サービス名>-<プロジェクト番号>.<リージョン>.run.app` で事前に決まり、`PUBLIC_BASE_URL` がないとサーバーは起動しない:
   ```bash
   gcloud run deploy google-health-mcp --source . --region asia-northeast1 --allow-unauthenticated \
     --set-env-vars "PUBLIC_BASE_URL=https://google-health-mcp-<プロジェクト番号>.asia-northeast1.run.app,GOOGLE_CLIENT_ID=<クライアントID>" \
     --set-secrets "MCP_SHARED_SECRET=MCP_SHARED_SECRET:latest,MCP_TOKEN_SIGNING_KEY=MCP_TOKEN_SIGNING_KEY:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,GOOGLE_REFRESH_TOKEN=GOOGLE_REFRESH_TOKEN:latest"
   ```
   `--allow-unauthenticated` は URL に到達できるようにするだけで、`/mcp` は OAuth で保護される。起動すると `curl <URL>/.well-known/oauth-authorization-server` が JSON を返す
5. Claude の Settings → Connectors → Add custom connector に `<URL>/mcp` を登録し、同意画面でパスフレーズを入力する

手動でコードを更新するときは、`--source . --region asia-northeast1` だけを付けて同じ `gcloud run deploy` を実行する。環境変数とシークレットは引き継がれる。マージのたびに自動でデプロイするなら、次の継続的デプロイを設定する。

## 継続的デプロイ（main へのマージ）

初回のデプロイ後は、`main` へのマージごとに `.github/workflows/deploy.yml` がデプロイする。CI（lint・テスト）が通ると GitHub Actions がイメージをビルドして Artifact Registry に push し、Cloud Run に新しいリビジョンを出す。環境変数とシークレットはサービスに設定済みのものを引き継ぐ。GitHub は Workload Identity Federation で認証するので鍵ファイルは置かない。初回だけ次を設定する:

```bash
PROJECT_ID=<プロジェクトID>
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
REPO=<オーナー>/<リポジトリ>
SA=github-deployer@$PROJECT_ID.iam.gserviceaccount.com

gcloud services enable iamcredentials.googleapis.com
gcloud iam service-accounts create github-deployer
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=roles/run.developer
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding $PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --member=serviceAccount:$SA --role=roles/iam.serviceAccountUser

gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github --location=global \
  --workload-identity-pool=github --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding $SA --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```

続けて GitHub 側を設定する:

1. Settings → Environments → New environment で `production` を作り、Deployment branches and tags で `main` だけを許可する。デプロイのたびに承認を挟みたいなら、Required reviewers に自分を追加する
2. Settings → Branches（または Rules）で `main` を保護し、マージ前に PR と `CI` のステータスチェックを必須にする
3. リポジトリ変数（Settings → Secrets and variables → Actions → Variables）を登録する。どれも秘密情報ではない。`GCP_WORKLOAD_IDENTITY_PROVIDER` が未設定の間はデプロイのジョブを飛ばす

Google Cloud はこのリポジトリの `main` のトークンだけを受け付け、GitHub は `main` にしか `production` 環境を使わせない。

| 変数 | 値 |
| --- | --- |
| `GCP_PROJECT_ID` | プロジェクト ID |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<プロジェクト番号>/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-deployer@<プロジェクトID>.iam.gserviceaccount.com` |
| `GCP_REGION` / `CLOUD_RUN_SERVICE` | 任意。省略時は `asia-northeast1` / `google-health-mcp` |

マージせずにデプロイしたいときは、`main` で Deploy ワークフローを手動実行する（Actions → Deploy → Run workflow）。

## Google のトークンの更新（同意画面が「テスト」のとき）

OAuth 同意画面の公開ステータスが「テスト」のままだと、Google の refresh_token は発行から7日で失効する。`scripts/refresh_google_token.sh` は、Secret Manager の最新のトークンが発行から5日経っていればブラウザを開いて再認可し（「許可」を押すのは人）、新しいトークンの登録・Cloud Run への反映・古い版の破棄までを行う。

```bash
scripts/refresh_google_token.sh           # 発行から5日以上なら更新する
scripts/refresh_google_token.sh --force   # 今すぐ更新する
scripts/install_refresh_schedule.sh       # macOS: launchd で毎日21時に確認する
```
