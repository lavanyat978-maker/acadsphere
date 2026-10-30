# End-to-end walkthrough

`walkthrough.cjs` drives every role through the real UI against a running stack,
checks the outcomes that matter, and records one captioned video per role:

| Video | What it covers |
| --- | --- |
| `1-intro` | What Praxis is |
| `2-teacher` | Syllabus PDF upload and retrieval search; practice set creation, AI generation, question editing, publishing; freezing and generating a sealed exam; uploading a prepared exam paper; a co-teacher's exam shown read-only |
| `3-student` | Enrolled practice list; timed attempt with autosave surviving a page reload; submission, result review, analytics, history; HOD pages refused |
| `4-hod` | MFA sign-in; department analytics; CSV import of accounts; enrolling a student; release ceremony (authorise, decrypt, print, mark released); audit chain verification; security events |
| `5-first-sign-in` | An imported student forced to replace the one-time password, then landing in their workspace |

Captions are drawn into the page (so they are in the picture) and also written as
`.srt` files, which `make-videos.sh` embeds as a subtitle track in each MP4.

## Run it

Start a **freshly seeded** demo stack first (the script builds on the seed data), e.g.

```bash
# backend (DEMO_MODE=true, empty database)
cd backend && alembic upgrade head && SEED_ON_STARTUP=true uvicorn app.main:app --port 8000
# frontend
cd frontend && npm run dev            # http://localhost:3001
```

Then:

```bash
cd e2e
npm install && npx playwright install chromium
npm run all                            # walkthrough + MP4 conversion (needs ffmpeg)
```

Output lands in `e2e/output/`: `results.txt` (pass/fail per check), per-role
`.webm`/`.srt`/`.mp4`, and `praxis-walkthrough.mp4` with every role in one video.
Point it at another URL with `BASE_URL=http://localhost:8080 npm run all` (Docker).
