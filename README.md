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

| Role | Capabilities |
| --- | --- |
| **Student** | Adaptive practice tests, timed attempts with autosave, per-attempt results, topic mastery analytics, attempt history. No access to papers. |
| **Teacher** | Upload syllabus documents (PDF/DOCX/TXT) and build practice tests; configure internal exams; trigger RAG-grounded generation; see validation reports; never sees a sealed paper. |
| **HOD** | Everything a teacher can do, plus department analytics, the release queue, decrypt-and-print inside a time window, mark papers released, and the tamper-evident audit log. |

Key product rules enforced in code, not documentation:

- A teacher who creates an exam still **cannot read the paper** afterwards. Generation
  ends with the paper sealed; only the HOD release flow opens it.
- Practice questions are visible to students; **exam questions are not**, ever, through
  any API a teacher or student token can reach.
- Printing is capped at three controlled copies per release and every copy is audited.

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
2. Open the Command Palette → **Tasks: Run Task** → `setup: create python venv`, then
   `setup: install frontend deps`. (One-time; a `.env` already exists at the repo root
   with development defaults.)
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

| Email | Role | MFA |
| --- | --- | --- |
| `hod@praxis.demo` | Head of Department | TOTP required |
| `teacher@praxis.demo` | Teacher | optional |
| `student@praxis.demo` | Student | optional |

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
cd backend && .venv/bin/python -m pytest        # 35 API + service tests
cd frontend && npm run build                    # typecheck + production build
```

The backend suite covers authentication and lockout, MFA, cookie/CSRF enforcement,
role isolation, the attempt lifecycle, the paper state machine, encryption and
integrity verification, validation rejection, and audit-chain tamper detection. All
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
