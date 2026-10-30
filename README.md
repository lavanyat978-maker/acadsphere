# Praxis — Secure Assessment Platform

Internal exam and practice platform for an academic department. It grounds question
generation in uploaded syllabus material, validates every generated question before it
can be used, and keeps an exam paper cryptographically sealed until the Head of
Department authorises its release.

Built for a real operational risk: the weakest link in a question-paper workflow is
usually a human with file-system access on the night before the exam. Praxis removes
plaintext from the database entirely, so "someone copied the paper" stops being a
silent possibility and becomes a detectable, audited event.

---

## What it does

Three roles, enforced on the server in `backend/app/services/access.py` (the UI only
hides what the API would refuse anyway):

| Area | Student | Teacher | HOD |
| --- | --- | --- | --- |
| Sign-in | Password, optional TOTP | Password, optional TOTP | Password + **mandatory** TOTP (enrolled at first sign-in) |
| Account | Own password and MFA | Own password and MFA | Own password; create, deactivate, reset any account |
| Subjects | Sees enrolled subjects | Works in assigned subjects; adds/edits topics there | Creates subjects, assigns teachers, enrols students |
| Syllabus library | — | Upload, search, delete own files (assigned subjects) | Everything, every subject |
| Practice tests | Attempts published tests in enrolled subjects, autosaved, server-timed; results, mastery, history | Creates and publishes in assigned subjects; views co-teachers' tests read-only; cohort analytics | Everything a teacher can, in every subject; department analytics |
| Internal exams | No access | Creates exams in assigned subjects, **AI-generated or uploaded PDF**; regenerates, replaces, cancels own exams; co-teachers' exams read-only; never reads a sealed paper | Everything a teacher can, in every subject |
| Release | — | — | Authorise, decrypt, print (3 stamped copies per paper), mark released — MFA + time window |
| Audit | — | — | Hash-chained audit log, chain verification, security events |

Key product rules enforced in code:

- An AI-generated paper is sealed before anyone can read it; the teacher who configured
  it never sees it. An uploaded paper is encrypted on receipt, so nobody other than its
  author can read it before release.
- Practice questions are visible to enrolled students; exam questions never reach any
  student or teacher API.
- Printing is capped at three numbered copies per paper, across every authorisation, and
  every copy is audited.

## Architecture

```
React 18 + Vite (role-aware SPA)
        │  HttpOnly session cookie + CSRF header
        ▼
FastAPI  ──┬── services/   auth, academic, syllabus, practice, exam, generation, paper, print, audit
           ├── validation/ five-level question validation pipeline
           ├── rag/        chunking, embeddings, hybrid retrieval
           ├── ai/         provider abstraction (OpenAI-compatible or deterministic demo)
           ├── encryption/ AES-256-GCM envelope encryption + key rotation
           └── storage/    local filesystem or S3-compatible object storage
        ▼
SQLAlchemy 2.0 → SQLite (dev) / PostgreSQL 16 (deploy), schema owned by Alembic
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (Docker)

Requires Docker with the Compose plugin.

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and a random JWT_SECRET, plus MASTER_KEY
docker compose up --build
```

To run without the demo institution, set `DEMO_MODE=false` and `SEED_ON_STARTUP=false`,
then create the first HOD inside the backend container:

```bash
docker compose exec backend python -m app.cli create-user --role HOD \
  --email hod@your-college.edu --name "Dr. Your Name"
```

It prints a one-time password. The HOD signs in, enrols an authenticator app, sets a new
password, and creates every other account from **People**.

- UI: <http://localhost:8080>
- API: <http://localhost:8000/api/health>, OpenAPI at <http://localhost:8000/docs>

The backend container runs `alembic upgrade head` before starting, so the schema is
versioned and reproducible. Demo data is seeded on first boot — the `.env` template sets
`SEED_ON_STARTUP=true`; set it to `false` once real users exist.

## Quick start (local development)

```bash
# backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # includes requirements.txt + pytest
alembic upgrade head
python -m app.seed                           # demo institution, subjects, exams
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm ci
npm run dev                                   # http://localhost:3001, proxies /api to :8000
```

## Running from VS Code

The workspace ships `.vscode/` configuration, so nothing needs to be typed into a
terminal:

1. Install the recommended extensions when prompted (Python, Pylance, debugpy).
2. Open the Command Palette → **Tasks: Run Task** → `setup: create .env`,
   `setup: create python venv`, then `setup: install frontend deps` (one-time). The
   VS Code tasks put the virtualenv at the repository root (`.venv`); the manual steps
   above use `backend/.venv` — either works.
3. **Run and Debug** → `▶ Full stack (API + Vite)` starts both processes in one click;
   the API listens on <http://127.0.0.1:8000> and Vite on <http://localhost:3001>, with
   `/api` proxied to the API. (3000 was moved to 3001 to avoid colliding with
   other dev servers; set `VITE_PORT` to pick a different one.)

Other launch configurations:

| Configuration | Use |
| --- | --- |
| `API: uvicorn (reload, :8000)` | Fast dev loop with auto-reload |
| `API: uvicorn + FastAPI debug (breakpoints, :8000)` | Same, with breakpoints on service code (`justMyCode`) |
| `DB: alembic upgrade head` | Apply migrations to the dev database |
| `DB: seed demo institution` | Create the demo users, subjects and exams (idempotent) |
| `Tests: backend pytest` | Run the backend suite |
| `Web: typecheck + production build` | `tsc --noEmit` against the SPA |

And tasks (Terminal → Run Task): `backend: migrate`, `backend: seed demo data`,
`backend: reset dev database`, `backend: test`, `frontend: build`.

Python Test Explorer discovers the backend tests automatically (`python.testing.pytestEnabled`).
On Windows, create the venv in `backend` with `py -m venv backend\.venv` and point
`python.defaultInterpreterPath` at it — the launch configs reference `.venv/bin/python`.

## Demo accounts

All demo accounts use the password `Praxis@2026#Demo` and exist only while
`DEMO_MODE=true` (production refuses to boot with demo mode on).

| Email | Role | Notes |
| --- | --- | --- |
| `hod@praxis.demo` | Head of Department | TOTP required (demo helper fills the code) |
| `teacher@praxis.demo` | Teacher | Assigned to DBMS and Operating Systems |
| `kavya@praxis.demo` | Teacher | Operating Systems only; owns the uploaded-paper demo exam |
| `student@praxis.demo` | Student | Enrolled in both subjects |
| `aman@praxis.demo` | Student | Enrolled in both subjects |
| `sara@praxis.demo` | Student | Enrolled in Operating Systems only |

The seeded "DBMS Internal Assessment 1" sits inside its release window for about 20
hours after seeding, so the HOD release flow can be tried straight away.

MFA is a real TOTP implementation. In demo mode the login screen shows the current
code for the account so a reviewer can complete the challenge; the code endpoint
(`POST /api/auth/mfa/demo-code`) is refused when `DEMO_MODE=false`.

`GET /api/meta` returns the demo credentials and the active AI mode, which is what the
login screen renders — it never exposes secrets, only the published demo fixtures.

## Generation modes

`LLM_PROVIDER=auto` picks **OpenAI** when `LLM_API_KEY` is set and the **deterministic
demo generator** otherwise. The demo generator is grounded in the same retrieved
syllabus chunks as the real one, so the pipeline, validation and sealing behaviour are
identical; only the wording of the questions differs. Production configuration
validation refuses to start with the mock provider.

## Tests

```bash
cd backend && .venv/bin/python -m pytest        # 65 API + service tests
cd frontend && npm test && npm run build        # unit tests, typecheck + production build
cd e2e && npm run all                            # browser walkthrough of every role, recorded as videos
```

`e2e/` drives the real UI for each role against a seeded stack, asserts the key outcomes
(including syllabus and exam-paper uploads and CSV import), and records captioned MP4s;
see [e2e/README.md](e2e/README.md).

The backend suite covers authentication and lockout (including MFA code guessing), the
first-HOD bootstrap and enrolment, forced password change, user administration,
cookie/CSRF enforcement, every role's allowed and denied paths (including co-teacher
read-only access and student enrolment scoping), autosave and the server-enforced
deadline, uploaded papers (sealing, replacement, stamped printing), generation failure
and retry, cancellation, the per-paper print cap, encryption and integrity verification,
validation rejection, and audit-chain tamper detection. All
tests run against an isolated SQLite database and never touch development data.

## Configuration

Every setting is an environment variable read by `backend/app/core/config.py`.
The important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENV` | `development` | `production` enables strict boot-time validation |
| `DATABASE_URL` | SQLite in `backend/var` | Use `postgresql+psycopg://…` in deployment |
| `JWT_SECRET` | dev placeholder | ≥32 random characters; required in production |
| `MASTER_KEY` | derived in dev | base64 of 32 bytes; required in production |
| `MASTER_KEYS_JSON` | – | `{"key-id": "<base64>"}` for key rotation |
| `DEMO_MODE` | `true` | Must be `false` in production |
| `COOKIE_SECURE` | `false` | Must be `true` in production (HTTPS) |
| `LLM_PROVIDER` / `LLM_API_KEY` | `auto` / empty | `auto` → demo generator without a key |
| `STORAGE_BACKEND` | `local` | `s3` for S3-compatible object storage |
| `TRUSTED_PROXY_COUNT` | `0` | Proxies in front of the API that append to `X-Forwarded-For` (1 behind the bundled nginx) |
| `MAX_PAPER_UPLOAD_MB` | `15` | Size limit for uploaded exam papers |
| `GEN_STALE_MINUTES` | `30` | A generation request silent for this long is abandoned so the exam can be retried |
| `RELEASE_WINDOW_MINUTES` | `120` | How long a release stays valid after the exam starts |
| `PRINT_EARLY_OPEN_MINUTES` | `1440` | How early the HOD may open the release |
| `SEED_ON_STARTUP` | `false` | Seed demo data on boot (demo mode only) |

## Repository layout

```
backend/
  app/api/routers/     HTTP layer (auth, academic, syllabus, practice, exams, analytics, audit, meta)
  app/services/        business rules and transactions
  app/validation/      five-level question validation
  app/rag/             ingestion + hybrid retrieval
  app/ai/              provider abstraction, prompts, embeddings
  app/encryption/      envelope encryption + key manager
  alembic/             versioned schema
  tests/               API and service tests
frontend/
  src/pages/           student / teacher / hod screens
  src/components/      design system, charts, app shell
  src/lib/             typed API client, formatting helpers
docs/                  architecture, security model, API reference
```

## Security summary

Session cookies are HttpOnly and CSRF-protected, passwords are Argon2id, login attempts
are rate-limited and locked out, MFA is TOTP, papers are sealed
with AES-256-GCM under a wrapped per-paper key, the audit log is a hash chain that
detects tampering, and access to exam plaintext is gated on an HOD release token issued
inside the exam window. Details, threat model and the production checklist live in
[docs/SECURITY.md](docs/SECURITY.md).

## Known limitations

- The demo generator is deterministic and intentionally simple; real question quality
  requires `LLM_API_KEY`.
- The key manager reads master keys from the environment. Production should move
  `KeyManager` onto a KMS/HSM — the interface is `wrap()`/`unwrap()` plus a key id, so
  nothing else changes.
- Generation runs in-process (`GEN_INLINE=true`), which is what makes the single-container
  demo possible. `generation_service.run_generation(request_id)` takes only a request id
  and touches no request-scoped state, so it is the seam for moving generation onto a
  queue and a real worker process at larger scale.
- The PDF renderer is a controlled print format, not a print-shop typesetter.
- Uploaded papers skip automated question validation; the HOD is the quality gate.
- Rate limiting is in-memory, so it is per process; run one API process (the default).
