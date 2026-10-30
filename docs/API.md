# API reference

All routes are served under `/api`. Interactive OpenAPI docs are at `/docs` in
non-production environments.

**Roles:** `STUDENT`, `TEACHER`, `HOD`. *Staff* means teacher or HOD. Where a teacher's
access depends on the subject: **(S)** = only subjects they are assigned to (view), **(O)**
= only items they created (change). The HOD passes every staff check for every subject.

**Auth.** Browsers use the HttpOnly `access_token` cookie plus `X-CSRF-Token` (mirroring the
`csrf_token` cookie, obtainable from `GET /auth/csrf`) on every state-changing request.
Non-browser clients may instead send the `access_token` from the login response as
`Authorization: Bearer …`.

Errors use one envelope everywhere:

```json
{ "error": "SUBJECT_NOT_ASSIGNED", "message": "You are not assigned to this subject.", "details": null }
```

`401` unauthenticated · `403` forbidden (role, subject, ownership, MFA, or
`PASSWORD_CHANGE_REQUIRED`) · `409` conflict or illegal state · `422` validation (`details`
lists fields) · `423` account locked · `429` rate limited (`Retry-After` header).

## Meta

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | public | Liveness, version, active AI mode |
| GET | `/meta` | public | Provider mode, limits; demo fixtures only in demo mode |

## Auth

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/login` | public | Email + password. Sets the session, or returns an MFA challenge; for an HOD without MFA, an **enrolment** challenge (`enrollment_required`, `secret`, `otpauth_uri`) |
| POST | `/auth/mfa/verify` | public | Challenge token + TOTP → session |
| POST | `/auth/mfa/enroll` | public | Enrolment token + first TOTP from the new device → MFA enabled + session |
| GET | `/auth/csrf` | public | Issue the double-submit CSRF token |
| GET | `/auth/me` | any | Current user, including `must_change_password` |
| POST | `/auth/logout` | any | Revoke all sessions and clear cookies |
| POST | `/auth/password` | any | Change password (required first when `must_change_password`) |
| POST | `/auth/mfa/setup` | any | Begin self-service TOTP enrolment |
| POST | `/auth/mfa/confirm` | any | Confirm enrolment with a code (signs the user out) |
| POST | `/auth/mfa/disable` | teacher, student | Disable MFA with a current code |
| POST | `/auth/mfa/challenge-code` | public | Demo mode only: current code for an HOD challenge |
| POST | `/auth/mfa/demo-code` | HOD | Demo mode only: current code for the signed-in demo HOD |

## Users (HOD)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/users?role=&q=` | List accounts with subjects, MFA and status |
| POST | `/users/import` | CSV (`email, full_name[, role, department, subjects]`, subjects as `CS501;CS502`). Validates every row first; on any error nothing is created and `details` lists the rows. Returns one-time passwords |
| POST | `/users` | Create an account; returns a one-time `temporary_password` unless one is supplied. `subject_ids` assigns teachers / enrols students |
| PATCH | `/users/{id}` | Edit name/department, activate or deactivate (ends sessions) |
| POST | `/users/{id}/reset-password` | New one-time password, sessions ended |
| POST | `/users/{id}/reset-mfa` | Clear the authenticator; the user enrols again at next sign-in |
| POST | `/users/{id}/unlock` | Clear a failed-login lockout |

The first HOD is created on the server: `python -m app.cli create-user --role HOD --email … --name …`.

## Subjects

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/subjects` | any | Staff: every subject with `assigned_to_me`; students: enrolled subjects only |
| POST | `/subjects` | HOD | Create a subject |
| PATCH | `/subjects/{id}` | HOD | Edit name, description, department |
| GET | `/subjects/teachers/all` | HOD | Teachers with their subject ids |
| GET | `/subjects/{id}/topics` | staff (S) | Topics |
| POST | `/subjects/{id}/topics` | staff (S) | Add a topic |
| PATCH | `/subjects/{id}/topics/{topic_id}` | staff (S) | Rename / move unit |
| DELETE | `/subjects/{id}/topics/{topic_id}` | staff (S) | Delete an unused topic (`409 TOPIC_IN_USE` otherwise) |
| POST | `/subjects/{id}/teachers/{teacher_id}` | HOD | Assign a teacher |
| DELETE | `/subjects/{id}/teachers/{teacher_id}` | HOD | Unassign a teacher (their content stays) |
| GET | `/subjects/{id}/students` | staff (S) | Enrolled students |
| POST | `/subjects/{id}/students` | HOD | Enrol students (`student_ids`) |
| DELETE | `/subjects/{id}/students/{student_id}` | HOD | Unenrol a student |

## Syllabus

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/syllabus/documents` | staff (S) | Upload PDF/DOCX/TXT; extracted, chunked, embedded, audited |
| GET | `/syllabus/documents?subject_id=` | staff (S) | Documents with chunk/char counts |
| DELETE | `/syllabus/documents/{id}` | uploader or HOD | Remove document and stored object |
| GET | `/syllabus/search?subject_id=&q=` | staff (S) | Retrieve grounding chunks |

## Practice

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/practice/tests` | student | Published tests in enrolled subjects |
| GET | `/practice/tests/{id}` | student | Test with questions, no answers (enrolled, published) |
| GET | `/practice/staff/tests` | staff | Own tests plus every test in assigned subjects; `can_edit` per row |
| GET | `/practice/staff/tests/{id}` | staff (S) | Test with answers |
| POST | `/practice/tests` | staff (S) | Create a draft (optionally with questions) |
| PATCH | `/practice/tests/{id}` | staff (O) | Edit title, description, duration |
| POST | `/practice/tests/{id}/status` | staff (O) | DRAFT ⇄ PUBLISHED ⇄ ARCHIVED; back to DRAFT only without attempts |
| POST | `/practice/tests/{id}/questions` | staff (O) | Add questions (draft only) |
| PUT | `/practice/tests/{id}/questions/{qid}` | staff (O) | Replace a question (draft only) |
| DELETE | `/practice/tests/{id}/questions/{qid}` | staff (O) | Delete a question (draft only) |
| POST | `/practice/tests/{id}/generate` | staff (O) | Generate MCQs from the syllabus (draft only) |
| GET | `/practice/tests/{id}/analytics` | staff (S) | Cohort performance per topic and per student |
| POST | `/practice/tests/{id}/attempts` | student | Start, or resume the open attempt (returns `saved_answers`) |
| GET | `/practice/attempts/{id}` | student | Own attempt; closes it if the deadline passed |
| PUT | `/practice/attempts/{id}/answers` | student | Autosave answers (`409 ATTEMPT_EXPIRED` after the deadline) |
| POST | `/practice/attempts/{id}/submit` | student | Grade and close; late submissions are graded from the autosave |
| GET | `/practice/attempts/{id}/result` | student | Own result with explanations |
| GET | `/practice/staff/attempts/{id}/result` | staff (S) | A student's result |
| GET | `/practice/history` | student | Own attempt history |
| GET | `/practice/analytics/me` | student | Own mastery and recommendations |

## Exams

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/exams` | staff (S) | Create an exam. `source`: `GENERATED` (full blueprint required) or `UPLOADED` (logistics + total marks) |
| GET | `/exams` | staff | Own exams plus every exam in assigned subjects; `can_edit` per row |
| GET | `/exams/{id}` | staff (S) | Exam with configuration, paper state and version |
| PUT | `/exams/{id}` | staff (O) | Replace a draft blueprint (source and subject fixed) |
| POST | `/exams/{id}/cancel` | staff (O) | Cancel; discards the paper unless it is already decrypted for printing |
| POST | `/exams/{id}/submit` | staff (O) | Freeze a generated exam's blueprint |
| POST | `/exams/{id}/generate` | staff (O) | Queue generation; also the retry after `FAILED`. Needs syllabus material (`409 NO_SYLLABUS`) |
| POST | `/exams/{id}/paper` | staff (O) | Upload or replace the PDF of an uploaded exam, until the release window opens |
| GET | `/exams/{id}/generation` | staff (S) | Generation status, counts, validation summary |
| GET | `/exams/{id}/paper` | staff (S) | Latest paper metadata and timeline; **never plaintext** |
| GET | `/exams/papers/{id}` | staff (S) | Same, by paper id |
| GET | `/exams/papers/{id}/plaintext` | staff | Always denied; audited |
| GET | `/exams/papers/{id}/integrity` | HOD + MFA | Decrypt in memory, verify hashes, return counts; audited |
| GET | `/exams/scheduled` | HOD | Every exam with release-window state |

### Release (HOD, MFA required)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/exams/release/queue` | Secured papers with window state, source, `prints_used`/`prints_remaining` |
| GET | `/exams/release/history` | Release records |
| POST | `/exams/release/{paper_id}/authorize` | Issue a release token for this session (audited) |
| POST | `/exams/release/{paper_id}/decrypt` | Verify integrity in memory; returns counts, never content |
| GET | `/exams/release/{paper_id}/pdf?release_token=` | Controlled print; max 3 per paper across all authorisations |
| POST | `/exams/release/{paper_id}/released` | Mark released and close the release |

## Analytics

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/analytics/dashboard` | any | Role-aware dashboard; the HOD's includes `department` per-subject and per-teacher rollups |
| GET | `/analytics/student` | student, HOD | Own analytics (HOD: department dashboard) |
| GET | `/analytics/student/{id}` | own student, HOD | Per-student analytics |

## Audit (HOD)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/audit/logs` | Paginated, filterable audit trail |
| GET | `/audit/verify` | Re-walk the hash chain; reports the first broken link |
| GET | `/audit/summary` | Event counts for a window |
| GET | `/audit/security-events` | Anomalies with severity and resolution state |
| POST | `/audit/security-events/{id}/resolve` | Mark an event resolved (audited) |
