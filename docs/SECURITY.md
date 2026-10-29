# Security model

**Threat model.** The realistic threats for this platform are: an insider or compromised
teacher account trying to read an exam paper before it is released, network attackers
targeting sessions and credentials, database-level leakage (a stolen dump or backup), and
prompt-injection or AI-generated garbage reaching students. This version does **not**
defend against a fully compromised application host, or against an attacker who already
holds the master key; those require the KMS-backed production notes below.

## Authentication

- **Argon2id** password hashing (`argon2-cffi`), with a dummy hash verification for
  unknown accounts so response timing does not reveal whether an email exists.
- Failed logins are counted per account: after `MAX_FAILED_LOGINS` (5) the account locks
  for `LOGIN_LOCK_MINUTES` (15) and an `ACCOUNT_LOCKED` security event is written.
- Login and MFA endpoints are rate limited per client (`RATE_LIMIT_LOGIN`,
  `RATE_LIMIT_MFA`), backed by a sliding-window limiter.
- Every outcome — success, unknown account, bad password, locked account, disabled
  account — is written to the append-only audit log.
- The session is an HS256 JWT in an **HttpOnly, SameSite** cookie. It is never returned
  in a response body, so it cannot be read by JavaScript and cannot leak through XSS.
  Tokens carry `tv` (token version); changing a password or enabling/disabling MFA
  increments the version and invalidates every previously issued token.
- Passwords require a minimum length and are checked against weak/common patterns.

## MFA (TOTP)

- RFC 6238 TOTP with a 30 s step and a ±1 window; the secret is stored encrypted at rest.
- Enrolment is a two-step confirm: setup returns an encrypted secret and otpauth URI for
  the authenticator app, confirmation requires a valid code before `mfa_enabled` flips.
- The HOD demo account ships with MFA **required**, and `POST /api/auth/mfa/challenge-code`
  issues a short-lived challenge code (TTL `MFA_CHALLENGE_TTL_MINUTES`, single use) so an
  HOD without an authenticator can still complete a demo review.
- `POST /api/auth/mfa/demo-code` is available only while `DEMO_MODE=true` and is refused
  in production.
- Known limitation: codes are not persisted as "used", so a code may be replayed within
  its 30 s validity window by anyone who intercepted it. Add a last-used-counter column
  before deploying to real students.

## CSRF and CORS

- A double-submit CSRF scheme: `GET /api/auth/csrf` issues an opaque token bound to the
  session, and `CSRFMiddleware` rejects state-changing requests whose `X-CSRF-Token`
  header does not match the cookie (constant-time comparison).
- CORS is allow-list only (`CORS_ORIGINS`), with credentials enabled so the cookie works
  cross-origin only for origins the deployment explicitly trusts.

## Session cookie hardening

| Property | Value |
| --- | --- |
| `httponly` | always on — no script access to the session |
| `samesite` | `lax` by default (`COOKIE_SAMESITE`), `strict` available |
| `secure` | `COOKIE_SECURE`, enforced in production (boot check) |
| `path` | `/` |

### Bearer fallback

The API also accepts the same token as `Authorization: Bearer`, which the CSRF
middleware deliberately exempts (a bearer header cannot be sent cross-site by a
browser's form or image navigation). The SPA sends the header only because some embedded
preview contexts — an iframe without `allow-same-origin`, or a cross-site preview proxy —
refuse to persist cookies, and a cookie-only client then appears to log in but 401s on
every following call. On a normal origin the cookie is set as well, and a deployment that
prefers cookie-only auth can simply ignore the header; if you drop cookie auth entirely,
keep the CSRF middleware as-is because it is the only thing protecting the cookie path.

## Encryption

**Envelope encryption** (AES-256-GCM), see `app/encryption/service.py`:

1. Each paper gets a fresh 256-bit data encryption key (DEK).
2. The DEK is wrapped by the active master key (KEK) and stored with its `key_id`.
3. The payload is encrypted with the DEK under a random 96-bit nonce; the AAD binds the
   paper id and version so ciphertext cannot be transplanted between rows.
4. The database stores only: wrapped DEK, key id, nonce, SHA-256 digests of the payload
   and the storage path of the ciphertext blob. **No plaintext paper is ever persisted.**
5. Integrity is verifiable without decryption (`verify_integrity`), and any tampering
   fails GCM authentication on read.

`key_id` supports rotation: new writes always use `MASTER_KEY_ID` while old ciphertext
stays readable through `MASTER_KEYS_JSON`. In development, when no master key is set, a
key is derived deterministically from `JWT_SECRET` so a restarted demo can still open its
own papers; production refuses to boot without explicit key material.

MFA secrets and any other sensitive fields are encrypted at rest with the same primitive.

## Paper release controls

- Plaintext exists only inside the release flow, reachable by **HOD only**.
- The release window opens `PRINT_EARLY_OPEN_MINUTES` before the exam and closes
  `RELEASE_WINDOW_MINUTES` after it starts; outside the window every decrypt attempt is
  rejected with a `RELEASE_WINDOW_CLOSED` audit event.
- Authorisation issues a **single-use token** bound to the paper, the HOD and the
  moment, with a short TTL (`RELEASE_TOKEN_TTL_MINUTES`). Reuse and expiry produce
  distinct security events.
- Printing is capped at three controlled copies per release; each PDF render increments
  the counter, is audited, and returns `prints_remaining`.
- Teachers hitting the plaintext route get a 403 *and* a `deny_plaintext_access` audit
  record, so attempted escalation is visible.
- Illegal state transitions (e.g. decrypting an unencrypted paper) are refused by the
  state machine and logged as `ILLEGAL_STATE_TRANSITION_BLOCKED`.

## Audit

- Every security-relevant event writes an append-only row to `audit_logs` containing
  actor, role, event, resource, IP, user agent, outcome and a redacted metadata payload
  (passwords, tokens and ciphertext are never logged; the formatter strips sensitive keys).
- Rows are a **hash chain**: each record hashes its predecessor, so removing or editing
  history breaks verification. `GET /api/audit/verify` re-walks the chain and reports
  the first broken link.
- `security_events` records auth anomalies separately (lockouts, MFA failures, release
  token rejections) with severity levels for monitoring.

## Upload safety

Syllabus uploads are extension- and magic-number-validated, size-capped, deduplicated by
SHA-256 per subject, parsed server-side, and stored in a pluggable object store rather
than the web root. Extraction failures roll back the row and delete the stored object.
The generator refuses to run against a subject with no ingested material, which also
prevents an ungrounded question set.

## Production hardening checklist

1. `ENV=production`, `DEMO_MODE=false`, `DEBUG=false`, `COOKIE_SECURE=true`.
2. `JWT_SECRET` from a secret manager, ≥32 random bytes.
3. `MASTER_KEY`/`MASTER_KEYS_JSON` supplied by KMS/HSM; replace `KeyManager` with the KMS
   adapter (`wrap()`/`unwrap()` plus key ids are the whole interface).
4. PostgreSQL instead of SQLite; enable TLS on the connection and encrypted backups.
5. `SEED_ON_STARTUP=false` and no demo accounts; create real users with MFA enforced.
6. Set `CORS_ORIGINS` to the deployed origin only; terminate TLS in front of the app.
7. Put the API behind a reverse proxy that forwards `X-Forwarded-For` honestly — rate
   limiting and audit records depend on the client IP.
8. Add the TOTP last-used-counter replay guard described above.
9. Ship `audit_logs` to append-only/WORM storage so the in-app chain is not the only copy.
