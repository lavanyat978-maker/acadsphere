# Security model

**Threat model.** The realistic threats for this platform are: an insider or compromised
teacher account trying to read an exam paper before it is released, network attackers
targeting sessions and credentials, database-level leakage (a stolen dump or backup), and
prompt-injection or AI-generated garbage reaching students. This version does **not**
defend against a fully compromised application host, or against an attacker who already
holds the master key; those require the KMS-backed production notes below.

## Roles and access control

Role rules live in one module, `app/services/access.py`, which routers and services call
instead of re-deriving them:

- **HOD** — a superset of a teacher in every subject, plus account administration,
  subject/enrolment management, paper release and the audit trail.
- **Teacher** — works inside the subjects they are assigned to. They can *view* everything
  in those subjects, including co-teachers' exams and practice tests, but can only
  *change* what they created.
- **Student** — sees published practice tests only for subjects they are enrolled in, and
  never any internal examination data.

Every denied role check writes an `UNAUTHORIZED_ACCESS_ATTEMPT` audit record. The full
matrix is in the README.

## Accounts

- There is no self-registration. The HOD creates accounts (`/api/users`); the first HOD of
  a deployment is created on the server with `python -m app.cli create-user --role HOD`.
- New accounts and HOD-initiated resets get a **one-time temporary password** shown once.
  Until it is changed, the API answers every route except `/auth/me`, `/auth/password` and
  `/auth/logout` with `403 PASSWORD_CHANGE_REQUIRED`.
- Deactivating an account, resetting its password or its authenticator increments its
  token version, ending every live session immediately. The HOD cannot deactivate their own
  account and the last active HOD cannot be deactivated.

## Authentication

- **Argon2id** password hashing, with a dummy hash verification for unknown accounts so
  response timing does not reveal whether an email exists.
- Passwords need at least `PASSWORD_MIN_LENGTH` (10) characters mixing three of: lower
  case, upper case, digits, symbols. Reusing the current password is refused.
- Failed logins **and failed MFA codes** count toward the same lockout: after
  `MAX_FAILED_LOGINS` (5) the account locks for `LOGIN_LOCK_MINUTES` (15), an
  `ACCOUNT_LOCKED` security event is written, and further attempts are refused before the
  password or code is even checked.
- Login and MFA endpoints are rate limited per client IP (`RATE_LIMIT_LOGIN`,
  `RATE_LIMIT_MFA`) with a sliding-window limiter.
- Every outcome is written to the append-only audit log.

### Sessions

- The session is an HS256 JWT in an **HttpOnly, SameSite** cookie. The SPA never stores
  it: page JavaScript (and therefore an XSS payload) cannot read it.
- Tokens carry `tv` (token version); password changes, MFA changes, logout, deactivation
  and HOD resets increment it and invalidate every earlier token.
- For non-browser API clients the login response also carries the token, and the API
  accepts it as `Authorization: Bearer`. Bearer requests are exempt from CSRF checks
  because a browser cannot attach that header cross-site. The SPA does not use this path.

### Client IP

Rate limits, lockout records and audit rows use the client IP. `X-Forwarded-For` is
honoured only for `TRUSTED_PROXY_COUNT` proxy hops, read from the right, so a client cannot
forge its address by sending the header itself. Set it to the number of proxies in front
of the API: `1` behind the bundled nginx, `2` for the Sites worker in front of Render, `0`
when the API is reached directly. Uvicorn runs with `--no-proxy-headers` so it does not
apply its own, more permissive, header handling.

## MFA (TOTP)

- RFC 6238 TOTP with a 30 s step and a ±1 step window. Secrets are sealed with AES-GCM
  under a key derived from `JWT_SECRET` (rotating `JWT_SECRET` therefore requires every
  user to re-enrol).
- **HOD: mandatory.** An HOD without an authenticator is not turned away at login: the
  password step returns an enrolment challenge (secret + `otpauth://` URI), and the session
  is issued only after `/auth/mfa/enroll` receives a valid code from the new device. The
  enrolment token is bound to the account's token version and cannot be replayed.
- **Teachers and students: optional**, self-service from the Account page. Disabling
  requires a current code; the HOD cannot disable their own MFA.
- A lost device is handled by another HOD resetting the authenticator (People screen); the
  user enrols a new one at the next sign-in.
- Demo helpers (`/auth/mfa/challenge-code`, `/auth/mfa/demo-code`) return the current code
  only while `DEMO_MODE=true`; production refuses to boot in demo mode.
- Known limitation: codes are not recorded as used, so an intercepted code can be replayed
  within its 30-90 s validity. Add a last-used-step column before high-stakes deployment.

## CSRF and CORS

- Double-submit CSRF: the readable `csrf_token` cookie must be mirrored in the
  `X-CSRF-Token` header on every state-changing cookie-authenticated request (constant-time
  comparison). The SPA fetches a token from `GET /api/auth/csrf` when the cookie is missing.
- CORS is allow-list only (`CORS_ORIGINS`), with credentials enabled.

## Encryption

**Envelope encryption** (AES-256-GCM), see `app/encryption/service.py`:

1. Each paper version gets a fresh 256-bit data encryption key (DEK).
2. The DEK is wrapped by the active master key (KEK) and stored with its `key_id`.
3. The paper body is encrypted with the DEK under a random 96-bit nonce; the AAD binds the
   paper id, version and exam id so ciphertext cannot be transplanted between rows.
4. The database stores only: wrapped DEK, key id, nonce, SHA-256 digests, content type and
   the storage path of the ciphertext blob.
5. After a generated paper is stored, the plaintext of **every** candidate question for
   that exam is removed from `question_pool` (selected and unselected alike, since rejected
   near-duplicates are as sensitive as the paper). Failed or cancelled generations are
   scrubbed the same way.

`key_id` supports rotation: new writes use `MASTER_KEY_ID` while old ciphertext stays
readable through `MASTER_KEYS_JSON`. In development, when no master key is set, a key is
derived from `JWT_SECRET` so a restarted demo can still open its own papers; production
refuses to boot without explicit key material.

## Exam papers

### Two sources, one release path

| | AI-generated paper | Uploaded paper |
| --- | --- | --- |
| Body | Canonical JSON, rendered to PDF only at print time | The teacher's PDF, byte for byte |
| Who has read the plaintext | Nobody until the HOD release | Its author (they wrote it) |
| Automated validation | 5 levels per question + whole paper | Not applicable; the HOD is the quality gate |
| Changeable | Retry after failure; never after sealing | Replaceable until the release window opens |

The honest guarantee for an uploaded paper is therefore: *nobody other than its author
can read it before the HOD releases it*, and every copy that leaves the system is counted
and stamped. Praxis cannot stop an author from sharing their own source file.

Uploaded PDFs must start with `%PDF-`, parse with `pypdf`, contain at least one page, be
under `MAX_PAPER_UPLOAD_MB`, and not be password protected.

### Lifecycle

The transition table in `app/models/enums.py` is the only legal path; anything else is
refused and audited as `ILLEGAL_STATE_TRANSITION_BLOCKED`. A paper that fails generation,
is replaced, or belongs to a cancelled exam moves to the terminal `DISCARDED` state and
its ciphertext is deleted; a retry or replacement always creates a new paper version.

### Release controls

- Release, decryption and printing are **HOD only**, require an MFA-verified session, and
  only work inside the release window (`PRINT_EARLY_OPEN_MINUTES` before the exam to
  `RELEASE_WINDOW_MINUTES` after it starts). Outside the window every attempt is rejected
  and audited.
- Authorisation issues a release token (TTL `RELEASE_TOKEN_TTL_MINUTES`) bound to the
  paper. It is valid for that release session — decrypt and the prints that follow — and
  is superseded by any later authorisation. Invalid and expired tokens produce distinct
  security events.
- Printing is capped at **three copies per paper version**, counted across every
  authorisation, so re-authorising never resets the count. Each copy is audited; uploaded
  PDFs are stamped on every page with "Controlled copy n/3" and the paper id.
- `GET /exams/papers/{id}/integrity` decrypts in memory to prove the ciphertext is intact,
  returns counts only, requires MFA, and is audited.
- An HOD who uploads, or authorises release of, an exam they created themselves raises an
  `HOD_OWN_PAPER` warning event, since no second person has seen that paper.
- Teachers hitting the plaintext route get a 403 and a `PLAINTEXT_PAPER_ACCESS_DENIED`
  audit record.

## Practice attempts

The timer is enforced by the server. Answers autosave during the attempt; a submission
arriving more than 30 s after the deadline is ignored and the attempt is graded from what
was saved before the deadline, so the timer cannot be bypassed by submitting late.

## Audit

- Every security-relevant event writes an append-only row to `audit_logs` containing
  actor, role, event, resource, IP, user agent, outcome and a redacted metadata payload
  (passwords, tokens, question text and ciphertext are never logged).
- Rows are a **hash chain**: each record hashes its predecessor, so removing or editing
  history breaks verification. `GET /api/audit/verify` re-walks the chain and reports the
  first broken link.
- `security_events` records anomalies separately with severity levels; the HOD can mark
  them resolved, which is itself audited.

## Upload safety

Syllabus uploads are extension- and magic-number-validated, size-capped, deduplicated by
SHA-256 per subject, parsed server-side off the event loop, and stored in a pluggable
object store rather than the web root. Extraction failures roll back the row and delete the
stored object. Exam generation is refused for a subject with no ingested material, so an
exam paper is never generated ungrounded.

## Production hardening checklist

1. `ENV=production`, `DEMO_MODE=false`, `DEBUG=false`, `COOKIE_SECURE=true`.
2. `JWT_SECRET` from a secret manager, ≥32 random characters.
3. `MASTER_KEY`/`MASTER_KEYS_JSON` supplied by KMS/HSM; replace `KeyManager` with the KMS
   adapter (`wrap()`/`unwrap()` plus key ids are the whole interface).
4. PostgreSQL instead of SQLite, with TLS and encrypted backups.
5. `SEED_ON_STARTUP=false`; create the first HOD with `python -m app.cli create-user`.
6. Set `CORS_ORIGINS` to the deployed origin only; terminate TLS in front of the app.
7. Set `TRUSTED_PROXY_COUNT` to the exact number of proxies in front of the API.
8. Add the TOTP replay guard described above.
9. Ship `audit_logs` to append-only/WORM storage so the in-app chain is not the only copy.
10. Run a single API process while `GEN_INLINE=true` (generation runs in-process and the
    in-memory rate limiter is per process); move generation to a worker before scaling out.
