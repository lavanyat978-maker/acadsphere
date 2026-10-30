# Deployment

Branches: work and test on `master`; merging `master` into `main` is the release.
`.github/workflows/deploy.yml` runs on every push to `main`:

1. **Tests**: the full CI workflow (backend on SQLite and Postgres, frontend). Nothing below runs if it fails.
2. **Image**: builds `backend/Dockerfile`, pushes `ghcr.io/<owner>/<repo>-backend:sha-<commit>`.
3. **Backend**: tells the host (Railway or Render, chosen by the `BACKEND_HOST` variable) to run that image, then waits until `/api/health` reports the same commit.
4. **Frontend**: builds the SPA, deploys it to Vercel, and checks `/api/health` *through* Vercel's proxy.
5. **Prune**: deletes all but the newest 3 image versions in GHCR. It runs last, so a failed deploy never deletes the images you would roll back to.

```
browser ── Vercel (static SPA, /api/* rewritten) ──► Railway (container) ──► Supabase Postgres
                                                                    └─────► Supabase Storage (S3)
                                                                    └─────► Gemini API
```

Keep the backend at **one replica**. Rate limits and background generation live inside the process.

## One-time setup

### 1. Supabase
- **Database**: Project → Connect → **Session pooler** string (port 5432, IPv4). Do not use the transaction pooler (6543); it breaks prepared statements. The direct connection is IPv6-only, and Railway may not be able to reach it. Paste the string as given: the app converts `postgresql://` to the psycopg driver itself. Append `?sslmode=require` if it is not already there.
- **Storage**: create a **private** bucket (e.g. `praxis-papers`). Project Settings → Storage → S3 Connection gives the endpoint and region; create an S3 access key pair there.

### 2. Gemini
Create an API key in Google AI Studio. Pick the chat and embedding model ids from AI Studio's model list.

### 3. Railway
1. New project → **Empty Service** (named e.g. `praxis-api`). Settings → Networking → **Generate Domain**. That URL is `BACKEND_URL`.
2. Set the variables below. Do **not** connect the GitHub repo as a source. The pipeline deploys the image, and a repo connection would also auto-deploy before tests pass.
3. Source → Docker image registry credentials: GHCR username and a personal access token with `read:packages` (the image is private). Or make the package public; it contains code but no secrets.
4. Note the service id and environment id (Cmd+K → copy ID, or from the URL).
5. Create an **account or workspace token** (railway.com/account/tokens).

| Variable | Value |
| --- | --- |
| `ENV` | `production` |
| `DEMO_MODE` | `false` |
| `DEBUG` | `false` |
| `SEED_ON_STARTUP` | `false` |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `lax` |
| `JWT_SECRET` | `openssl rand -base64 48` |
| `MASTER_KEY` | `openssl rand -base64 32`. **Back it up outside Railway.** Losing it makes every stored paper unreadable. |
| `MASTER_KEY_ID` | e.g. `key-2026-01` |
| `DATABASE_URL` | Supabase session-pooler string |
| `LLM_PROVIDER` | `openai` (means "OpenAI-compatible API") |
| `LLM_API_KEY` | Gemini key |
| `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `LLM_MODEL` | `gemini-2.5-flash` (pin a stable id; avoid `-latest` and `preview`) |
| `EMBEDDING_MODEL` | `gemini-embedding-001` |
| `EMBEDDING_API_DIMENSIONS` | `768` (decide once: changing it later means re-embedding everything) |
| `STORAGE_BACKEND` | `s3` |
| `S3_BUCKET` / `S3_ENDPOINT_URL` / `S3_REGION` | from Supabase |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | from Supabase |
| `CORS_ORIGINS` | your Vercel URL, e.g. `https://praxis.vercel.app` |
| `TRUSTED_PROXY_COUNT` | `2` (Vercel, then Railway). Verify, see below. |
| `GEN_INLINE` | `true` |

Railway sets `PORT` itself.

### 3b. Render instead of Railway
Use this if Railway is unavailable. Set the GitHub variable `BACKEND_HOST` to `render`; unset (or anything else) means Railway.

Free-tier facts (render.com/docs/free): the service sleeps after 15 minutes without traffic and takes about a minute to wake; it can restart at any time, which interrupts a running generation (the exam is marked failed and can be retried); there is no shell and no persistent disk. Fine for a demo; use a paid always-on instance for real exams.

1. Let the pipeline build the first image: merge to `main` (the deploy step will fail, which is expected), or run the **Deploy** workflow manually. The image `ghcr.io/<owner>/<repo>-backend:latest` now exists. Make the package public (GitHub → Packages → Package settings), or add GHCR registry credentials in Render.
2. Render → **New → Web Service → Existing image**. Image URL: `ghcr.io/<owner>/<repo>-backend:latest` (lowercase). Instance type: Free. Health check path: `/api/health`.
3. Add the same environment variables as the Railway table above. Leave `PORT` unset; Render provides it and the container honours it.
4. Settings → **Deploy Hook**: copy the URL. It is a secret.
5. GitHub: secret `RENDER_DEPLOY_HOOK_URL`; variables `BACKEND_HOST=render` and `BACKEND_URL=https://<service>.onrender.com`. `RAILWAY_*` are not needed.
6. Re-run the **Deploy** workflow. The deploy hook passes the exact `sha-…` tag; Render rejects it (HTTP 400) if the image path differs from the service's image URL.

**First admin without a shell** (works for any host): run the CLI from your computer with Docker, pointed at the production database:

```bash
docker run --rm -e ENV=production -e DEMO_MODE=false -e COOKIE_SECURE=true \
  -e JWT_SECRET=... -e MASTER_KEY=... -e LLM_PROVIDER=openai -e LLM_API_KEY=... \
  -e DATABASE_URL='postgresql://…?sslmode=require' \
  ghcr.io/<owner>/<repo>-backend:latest \
  python -m app.cli create-user --role HOD --email you@college.edu --name "Your Name"
```
(Production mode refuses to start without the security settings, hence the extra variables. Use the same values as the service.)

### 4. Vercel
Create a project from this repository but **leave Root Directory empty**, and keep Git auto-deploys off (`frontend/vercel.json` already disables them). The pipeline runs the Vercel CLI inside `frontend/`. Framework preset: Vite. No environment variables are needed.

### 5. GitHub
Settings → Environments → create `production` (optionally add required reviewers for a manual approval gate).

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `RAILWAY_TOKEN` | Railway token |
| Secret | `VERCEL_TOKEN` | Vercel account token |
| Secret | `VERCEL_ORG_ID` | from `.vercel/project.json` after `vercel link` |
| Secret | `VERCEL_PROJECT_ID` | same |
| Variable | `RAILWAY_SERVICE_ID` | Railway service id |
| Variable | `RAILWAY_ENVIRONMENT_ID` | Railway environment id |
| Variable | `BACKEND_URL` | `https://<your-service>.up.railway.app` |

Settings → Actions → General → Workflow permissions must allow the workflow's `packages: write` (the default token can; org policy may restrict it).

### 6. Check the services before the first deploy
With the same values in a local `backend/.env` (never committed):

```bash
cd backend && python -m app.cli check          # database, storage, llm — prints PASS/FAIL, never secrets
```

### 7. First deploy and first admin (Railway)
Merge `master` into `main`. When the pipeline is green, create the first HOD against production:

```bash
railway run --service praxis-api python -m app.cli create-user --role HOD --email you@college.edu --name "Your Name"
```

The command prints a one-time password. At first sign-in the HOD sets a new password and enrols an authenticator app.

## Verify after the first deploy
- **Proxy hops**: sign in from a known IP and read the audit log's IP; if it shows a Vercel/Railway address, change `TRUSTED_PROXY_COUNT`.
- **Upload size**: upload a ~10 MB syllabus PDF through the site. Vercel limits request bodies on rewrites; if it is rejected, lower `MAX_UPLOAD_MB` and `MAX_PAPER_UPLOAD_MB` or move uploads to the Railway domain.
- **Gemini**: generate a practice test and an exam paper. Failed generations show their reason on the exam page.

## Rollback
Re-run the **Deploy** workflow manually on an older commit only if its image still exists (the newest 3 are kept), or point the Railway service at an older tag from the dashboard.

## Image retention
`prune-images` keeps the newest 3 package versions. `provenance: false` is set on the build so each push is exactly one version. If you see more than 3 afterwards, check the package page for untagged versions.
