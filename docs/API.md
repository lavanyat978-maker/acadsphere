# API reference

All routes are served under `/api`. Interactive OpenAPI docs are at `/docs` in
non-production environments.

**Roles:** `student`, `teacher`, `HOD`. *staff* means teacher or HOD.
**Auth:** session cookie (`access_token`, HttpOnly) plus `X-CSRF-Token` on every
state-changing request. Responses never carry the token in the body; call
`GET /auth/csrf` first and mirror the `csrf_token` cookie into the header.

Errors use one envelope everywhere:

```json
{ "error": { "code": "SUBJECT_NOT_ASSIGNED", "message": "You are not assigned to this subject." }, "request_id": "…" }
```

`401` unauthenticated, `403` authorised-but-forbidden (or MFA missing),
`409` conflict/illegal state, `429` rate limited (with `Retry-After`).

## Meta

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | public | Liveness, version, active AI mode |
| GET | `/meta` | public | Deployed demo fixtures and provider mode (login screen) |

## Auth

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/login` | public | Email + password. Sets the session cookie, or returns an MFA challenge |
| POST | `/auth/mfa/verify` | public | Exchange challenge + TOTP for the session |
| GET | `/auth/csrf` | public | Issue the double-submit CSRF token |
| GET | `/auth/me` | any | Current principal, role, MFA state |
| POST | `/auth/logout` | any | Revoke and clear the session |
| POST | `/auth/password` | any | Change password (increments token version → invalidates other sessions) |
| POST | `/auth/mfa/setup` | any | Begin TOTP enrolment; returns encrypted secret + otpauth URI |
| POST | `/auth/mfa/confirm` | any | Confirm enrolment with a valid code |
| POST | `/auth/mfa/challenge-code` | any | One-time HOD challenge code for demo review |
| POST | `/auth/mfa/demo-code` | any | Demo-only current TOTP value (`DEMO_MODE=true` only) |

## Academic

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/subjects` | any | Subjects, topics, assigned teachers, `assigned_to_me` |
| POST | `/subjects` | HOD | Create a subject with teacher assignments |
| GET | `/subjects/{id}/topics` | any | Topics for a subject |
| POST | `/subjects/{id}/topics` | staff | Add a topic |
| POST | `/subjects/{id}/teachers/{teacher_id}` | HOD | Assign a teacher to a subject |
| GET | `/subjects/teachers/all` | HOD | All teachers for assignment pickers |

## Syllabus

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/syllabus/documents` | staff | Upload PDF/DOCX/TXT; extracted, chunked, embedded, audited |
| GET | `/syllabus/documents` | staff | Document list with chunk/char counts and status |
| DELETE | `/syllabus/documents/{id}` | staff | Remove document and its stored object (uploader or HOD) |
| GET | `/syllabus/search` | staff | Retrieve grounding chunks for a query (also validates uploads) |

## Practice

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/practice/tests` | student | Published tests available to attempt |
| GET | `/practice/staff/tests` | staff | All tests, including drafts |
| POST | `/practice/tests` | staff | Create a test (blueprint: subjects, topics, difficulty, count) |
| GET | `/practice/tests/{id}` | student | Test detail with questions, no answers |
| GET | `/practice/staff/tests/{id}` | staff | Test detail with answers and provenance |
| PATCH | `/practice/tests/{id}` | staff | Edit metadata, topics, counts |
| POST | `/practice/tests/{id}/status` | staff | Draft → published → archived |
| POST | `/practice/tests/{id}/questions` | staff | Add hand-written questions |
| POST | `/practice/tests/{id}/generate` | staff | Generate questions from ingested syllabus |
| GET | `/practice/tests/{id}/analytics` | staff | Cohort performance per topic |
| POST | `/practice/attempts/{id}/submit` | student | Grade and close an attempt |
| GET | `/practice/attempts/{id}/result` | student | Own result with explanations |
| GET | `/practice/staff/attempts/{id}/result` | staff | Any submitted attempt |
| GET | `/practice/history` | student | Own attempt history |
| GET | `/practice/analytics/me` | student | Own mastery and recommendations |

## Exams

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/exams` | teacher | Configure an internal exam (subject, topics, marks, blueprint) |
| GET | `/exams` | staff | Exam list scoped to the caller's subjects |
| GET | `/exams/{id}` | staff | Exam detail with configuration and state |
| POST | `/exams/{id}/submit` | teacher | Lock configuration for generation |
| POST | `/exams/{id}/generate` | teacher | Queue (or run inline) paper generation |
| GET | `/exams/{id}/generation` | staff | Generation status, counts, validation summary |
| GET | `/exams/{id}/paper` | staff | Paper metadata + integrity; **never plaintext** |
| GET | `/exams/papers/{id}` | staff | Same, by paper id |
| GET | `/exams/papers/{id}/integrity` | HOD | Digest and key-id verification |
| GET | `/exams/papers/{id}/plaintext` | staff | Denied for teachers; audited attempt |
| GET | `/exams/scheduled` | staff | Upcoming exams with release-window state |

### Release (HOD, MFA required)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/exams/release/queue` | Papers eligible for release, with window and print state |
| GET | `/exams/release/history` | Completed releases |
| POST | `/exams/release/{paper_id}/authorize` | Issue a single-use release token (audited) |
| POST | `/exams/release/{paper_id}/decrypt` | Decrypt once with the token; payload returned here only |
| GET | `/exams/release/{paper_id}/pdf` | Controlled print; max 3 copies, each audited |
| POST | `/exams/release/{paper_id}/released` | Mark released and close the release |

## Analytics

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/analytics/dashboard` | any | Role-aware dashboard summary |
| GET | `/analytics/student` | any | Own mastery, strengths, weak topics |
| GET | `/analytics/student/{id}` | staff | Per-student view (teachers: own subjects only) |

## Audit (HOD)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/audit/logs` | Paginated, filterable audit trail |
| GET | `/audit/verify` | Re-walk the hash chain; reports the first broken link |
| GET | `/audit/summary` | Event counts by type and day |
| GET | `/audit/security-events` | Auth anomalies with severity |
