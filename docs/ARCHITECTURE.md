# Architecture

Praxis is a single FastAPI service plus a React SPA. The interesting part is not the
web plumbing — it is the ordering guarantee: **content is generated, validated,
assembled, locked and encrypted before anyone with a browser session can read it, and
the only way to read it afterwards is through an auditable, time-boxed release.**

## Layers

```
app/api/routers/      HTTP: authentication, request parsing, role guards, response shapes
app/services/         Transactions and business rules (one module per bounded area);
                      access.py is the single source of role/subject/ownership rules
app/validation/       5-level question quality gate
app/rag/              Document extraction → chunking → embedding → retrieval
app/ai/               LLM provider abstraction, prompts, embedder selection
app/encryption/       Envelope encryption + key manager
app/storage/          Local filesystem or S3-compatible object store
app/analytics/        Mastery, difficulty calibration, department rollups
app/models/           SQLAlchemy tables and enums (state machines live in enums.py)
app/core/             Settings, security primitives, errors, middleware, demo fixtures
```

Routers never talk to the database directly: they resolve the caller, enforce the role,
and delegate to a service. Services ask `app/services/access.py` whether the caller may
view or change a subject, exam or practice test (HOD: everything; teacher: view within
assigned subjects, change only own items; student: enrolled subjects only). Services own commits and audit records, which is what makes
"every mutation is audited" enforceable rather than aspirational.

## Data model

```
users ──< subject_teachers >── subjects ──< topics
  ├──< student_subjects >──────────┘
  │                              │           │
  │                              │           └──< exam_topics / practice_test_topics
  │                              └──< syllabus_documents ──< document_chunks (embeddings)
  ├──< practice_tests ──< practice_questions ──< practice_attempts ──< practice_answers
  ├──< internal_exams ──< exam_configurations
  └──< question_papers ──< encrypted_papers, paper_releases, paper_state_transitions
                              └──< question_pool, validation_results, question_metadata
audit_logs            append-only hash chain (seq, prev_hash, hash)
security_events       auth/authorisation anomalies
generation_requests   async job records for the generation pipeline
```

Answer keys live only in `practice_questions.correct_option` / `question_pool.correct_option`
and are never serialised into a student-facing response. Exam payloads additionally live
only inside `encrypted_papers` (ciphertext blob reference), never in the paper row.

## Request lifecycle

1. `RequestLogMiddleware` assigns a request id and structured log context.
2. `SecurityHeadersMiddleware` adds CSP, framing and MIME protections.
3. `CSRFMiddleware` requires a double-submit `X-CSRF-Token` matching the `csrf_token`
   cookie for every state-changing method; the token is issued by `GET /api/auth/csrf`.
4. Authentication resolves the session cookie (`access_token`) to a user, rejects
   tokens issued before a password change, and applies per-route rate limits.
5. The router applies the role guard, then calls a service inside a single transaction.

## Practice flow

A practice test declares subjects, topics, difficulty mix and question count. Questions
are produced by the same generator but bypass the sealing path: they are meant to be
seen. An attempt is created with a server-side timer; answers are autosaved
(`PUT /practice/attempts/{id}/answers`) and restored on reload; submission grades
answers, computes topic-level mastery and writes a result. A submission arriving after
the deadline (plus 30 s grace) is ignored and the autosaved answers are graded instead;
overdue attempts are closed the same way when anyone next reads them. Teachers see aggregate
analytics per test, never per-student answer text for a test the student has not
submitted.

## Generation pipeline

```
exam submitted
   → generation_request row (status QUEUED)
   → RAG retrieval: top-k syllabus chunks per topic (cosine over embeddings)
   → provider generates N candidates per topic, each carrying its source chunk ids
   → validation levels 1-4 per candidate (reject / accept)
   → assemble paper from accepted candidates to the requested blueprint
   → validation level 5 on the whole paper (marks, coverage, difficulty balance)
   → paper LOCKED → AES-256-GCM encrypted → ciphertext stored → paper STORED
   → generation_request COMPLETED with counts: generated / rejected / accepted
```

`GEN_INLINE=true` runs the job in-process right after the request is accepted, which is
what makes the demo single-container. The same function is the seam for an external
worker: it takes a request id and never depends on request-scoped state.

### Validation, level by level

| Level | Check | Failure mode |
| --- | --- | --- |
| 1 Rules | Stem/options present, option count, marks, Bloom/difficulty allowed, prohibited patterns (prompt-injection, placeholders, URLs), no answer leaked in the stem | Candidate rejected with a machine-readable issue code |
| 2 Language | Spelling against an academic dictionary, sentence length, duplicate option text, casing, trailing garbage | Candidate rejected |
| 3 Semantics | Embedding similarity between the question and its cited syllabus chunk; the answer must be supported by the source | Candidate rejected as ungrounded |
| 4 Duplicates | Cosine similarity against already-accepted candidates and the existing question pool | Candidate rejected as duplicate |
| 5 Whole paper | Section marks sum, topic coverage against the blueprint, difficulty distribution, total marks, no repeated stem | Paper rejected and regenerated |

Issue messages deliberately never include question text, so a teacher can see *why* a
candidate failed without seeing the candidate. That keeps generation telemetry usable
in the same UI that must not leak paper content.

## Uploaded papers

An exam created with `source=UPLOADED` skips generation. The teacher uploads a PDF to
`POST /exams/{id}/paper`; it is checked (`%PDF-` header, parses, has pages, not password
protected, size limit), the configuration is frozen, and a new paper version goes
`DRAFT → SUBMITTED → LOCKED → ENCRYPTED → STORED` with the PDF bytes as the encrypted body.
Until the release window opens the teacher may upload again: the previous version is
`DISCARDED` and its ciphertext deleted. At print time the HOD gets the original PDF with
a "Controlled copy n/3" stamp on every page.

## Paper lifecycle

Generated: `DRAFT → SUBMITTED → GENERATING → VALIDATING → ASSEMBLED → VALIDATED → LOCKED →
ENCRYPTED → STORED → RELEASE_PENDING → DECRYPTED → PRINTED → RELEASED`

Uploaded: `DRAFT → SUBMITTED → LOCKED → ENCRYPTED → STORED → …` (same release states)

Any state before `DECRYPTED` may move to the terminal `DISCARDED` (failed generation,
replaced upload, cancelled exam). Every failure path in `generation_service.mark_failed`
discards the half-built paper and scrubs candidate plaintext, so a retry always starts a
fresh paper version and an exam is never stuck mid-pipeline. Requests orphaned by a
restart are failed at startup.

The transition table in `app/models/enums.py` is the only legal path; `transition()`
refuses anything else, writes an `ILLEGAL_STATE_TRANSITION_BLOCKED` audit record and
raises a 409. Every accepted transition writes a `paper_state_transitions` row *and* an
audit entry, so the timeline can be reconstructed from two independent sources.

Once `LOCKED`, the plaintext is gone: the assembled payload is encrypted under a fresh
per-paper data key, that key is wrapped by the active master key, and only the wrapped
key, nonce, key id and SHA-256 digests are stored. Teachers calling the paper endpoint
receive metadata and integrity information only — `deny_plaintext_access()` records an
audit event if anyone tries the plaintext route.

## Release flow (HOD only)

```
HOD opens /api/exams/release/queue
  → exam window check (opens PRINT_EARLY_OPEN_MINUTES before, closes RELEASE_WINDOW_MINUTES after)
  → POST /release/{paper_id}/authorize  → single-use release token (TTL 10 min), audited
  → POST /release/{paper_id}/decrypt    → token + window validated, paper DECRYPTED, payload returned once
  → GET  /release/{paper_id}/pdf        → controlled print, max 3 copies per paper (all
                                          authorisations combined), each audited
  → POST /release/{paper_id}/released   → paper RELEASED, release closed
```

The token is bound to the paper, the HOD and the authorisation moment; expiry, reuse and
out-of-window attempts each produce a distinct security event. Decryption is the single
place in the codebase where paper plaintext is materialised, and it is reachable by one
role inside one window.

## Frontend

A role-aware SPA: `/student/*`, `/teacher/*` (teacher and HOD) and `/hod/*` route trees
guarded by the session role, plus `/account` for everyone. A user holding a temporary
password is held on `/account` until they change it. The session is cookie-only; the API
client mirrors the CSRF cookie into the `X-CSRF-Token` header and fetches one when it is
missing. Charts are hand-rolled SVG (no chart dependency), and the design
system in `src/styles.css` uses a single token set so the three role experiences stay
visually consistent.
