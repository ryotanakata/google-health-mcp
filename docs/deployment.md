# Deployment

[日本語版はこちら](deployment.ja.md)

1. In Google Cloud, enable the Google Health API. Set up the OAuth consent screen (user type **External**) with the three read-only scopes in `GOOGLE_HEALTH_SCOPES`, keep it in **Testing**, and add your own Google account as a test user. No app verification or website is needed, but the refresh token expires after 7 days (see below)
2. Create an OAuth client of type **Desktop app** and save its JSON as `client_secret.json` in the repository root (it is git-ignored)
3. Run `python auth_setup.py --client-secret client_secret.json` and store the four secrets in Secret Manager. Paste values at a hidden prompt so they stay out of your shell history (zsh syntax, the macOS default; in bash use `read -rsp "client secret: " V`):
   ```bash
   printf '%s' "$(openssl rand -hex 24)" | gcloud secrets create MCP_SHARED_SECRET --data-file=-
   printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create MCP_TOKEN_SIGNING_KEY --data-file=-
   read -rs "V?client secret: "; printf '%s' "$V" | gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-; unset V
   read -rs "V?refresh token: "; printf '%s' "$V" | gcloud secrets create GOOGLE_REFRESH_TOKEN --data-file=-; unset V
   ```
   Read the passphrase back with `gcloud secrets versions access latest --secret=MCP_SHARED_SECRET` and keep it in a password manager. Then let Cloud Run read the secrets: grant `roles/secretmanager.secretAccessor` to `<project-number>-compute@developer.gserviceaccount.com`
4. Deploy once. The Cloud Run URL is known in advance (`https://<service>-<project-number>.<region>.run.app`), and the server does not start without `PUBLIC_BASE_URL`:
   ```bash
   gcloud run deploy google-health-mcp --source . --region asia-northeast1 --allow-unauthenticated \
     --set-env-vars "PUBLIC_BASE_URL=https://google-health-mcp-<project-number>.asia-northeast1.run.app,GOOGLE_CLIENT_ID=<client-id>" \
     --set-secrets "MCP_SHARED_SECRET=MCP_SHARED_SECRET:latest,MCP_TOKEN_SIGNING_KEY=MCP_TOKEN_SIGNING_KEY:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,GOOGLE_REFRESH_TOKEN=GOOGLE_REFRESH_TOKEN:latest"
   ```
   `--allow-unauthenticated` only makes the URL reachable; `/mcp` is still protected by the OAuth flow. `curl <URL>/.well-known/oauth-authorization-server` returns JSON once it is up
5. In Claude: Settings → Connectors → Add custom connector → `<URL>/mcp`, then enter the passphrase on the consent screen

To ship code changes by hand, run the same `gcloud run deploy` with only `--source . --region asia-northeast1`; environment variables and secrets are kept. To deploy on every merge instead, set up continuous deployment below.

## Continuous deployment (merge to main)

After the first deploy, `.github/workflows/deploy.yml` ships every merge to `main`: once CI (lint and tests) passes, GitHub Actions builds the image, pushes it to Artifact Registry, and deploys a new Cloud Run revision. Environment variables and secrets stay as configured on the service. GitHub authenticates with Workload Identity Federation, so no key file is stored. Set it up once:

```bash
PROJECT_ID=<project-id>
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
REPO=<owner>/<repo>
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

On GitHub:

1. Settings → Environments → New environment `production`. Under Deployment branches and tags, allow only `main`. Add yourself as a required reviewer if every deploy should wait for approval
2. Settings → Branches (or Rules) → protect `main`: require a pull request and the `CI` status check before merging
3. Add these repository variables (Settings → Secrets and variables → Actions → Variables). None of them is a secret. Until `GCP_WORKLOAD_IDENTITY_PROVIDER` is set, the deploy job is skipped

Google Cloud only accepts tokens from `main` of this repository, and GitHub only lets `main` use the `production` environment.

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | Project ID |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<project-number>/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-deployer@<project-id>.iam.gserviceaccount.com` |
| `GCP_REGION` / `CLOUD_RUN_SERVICE` | Optional. Default `asia-northeast1` / `google-health-mcp` |

To deploy without a merge, run the Deploy workflow manually (Actions → Deploy → Run workflow) on `main`.

## Renewing the Google token (consent screen in Testing)

While the OAuth consent screen stays in **Testing**, Google revokes the refresh token 7 days after it is issued. `scripts/refresh_google_token.sh` renews it once the latest token in Secret Manager is 5 days old: it opens the browser for you to click Allow, stores the new token, updates Cloud Run, and destroys the old versions.

```bash
scripts/refresh_google_token.sh           # renew if the token is 5+ days old
scripts/refresh_google_token.sh --force   # renew now
scripts/install_refresh_schedule.sh       # macOS: check daily at 21:00 with launchd
```
