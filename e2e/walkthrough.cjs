/**
 * Praxis end-to-end walkthrough: drives every role through the real UI against a
 * running stack, asserts the important outcomes, and records one captioned video
 * per role (WebM + SRT subtitles; converted to MP4 by `npm run videos`).
 *
 *   BASE_URL=http://localhost:3001 node walkthrough.cjs
 *
 * Expects a freshly seeded demo database (DEMO_MODE=true): the script builds on
 * the seeded subjects, users and the release-window exam.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE_URL || "http://localhost:3001";
const OUT = path.resolve(process.env.OUT_DIR || path.join(__dirname, "output"));
const FIXTURES = path.join(__dirname, "fixtures");
const PASSWORD = "Praxis@2026#Demo";
const SIZE = { width: 1280, height: 720 };

fs.mkdirSync(OUT, { recursive: true });
const results = [];
const problems = [];

function check(name, ok, detail = "") {
  results.push(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) console.error(`FAIL ${name} ${detail}`);
}

// --- Captions -----------------------------------------------------------------
// A caption bar is drawn into the page (so it is burned into the video) and every
// caption is also written to an SRT file with its timing.
const CAPTION_SCRIPT = () => {
  const draw = () => {
    if (!document.body) return;
    let bar = document.getElementById("__walkthrough_caption");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "__walkthrough_caption";
      bar.style.cssText =
        "position:fixed;left:50%;bottom:22px;transform:translateX(-50%);max-width:86%;z-index:2147483647;" +
        "background:rgba(11,27,40,.88);color:#fff;font:600 17px/1.45 Inter,system-ui,sans-serif;padding:10px 18px;" +
        "border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.25);text-align:center;pointer-events:none";
      document.body.appendChild(bar);
    }
    let tag = document.getElementById("__walkthrough_role");
    if (!tag) {
      tag = document.createElement("div");
      tag.id = "__walkthrough_role";
      tag.style.cssText =
        "position:fixed;right:16px;top:14px;z-index:2147483647;background:#0d8a77;color:#fff;" +
        "font:700 12px/1 Inter,system-ui,sans-serif;letter-spacing:.08em;padding:7px 10px;border-radius:999px;pointer-events:none";
      document.body.appendChild(tag);
    }
    const text = sessionStorage.getItem("__caption") || "";
    bar.textContent = text;
    bar.style.display = text ? "block" : "none";
    tag.textContent = sessionStorage.getItem("__role") || "";
  };
  document.addEventListener("DOMContentLoaded", draw);
  setInterval(draw, 250);
};

function srtTime(ms) {
  const pad = (value, size = 2) => String(Math.floor(value)).padStart(size, "0");
  return `${pad(ms / 3600000)}:${pad((ms / 60000) % 60)}:${pad((ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}

async function startRole(browser, role, label) {
  const context = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: path.join(OUT, "raw"), size: SIZE },
    acceptDownloads: true,
  });
  await context.addInitScript(CAPTION_SCRIPT);
  const page = await context.newPage();
  page.on("pageerror", (error) => problems.push(`[${role}] ${error.message}`));
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 500) {
      problems.push(`[${role}] ${response.status()} ${response.url()}`);
    }
  });
  const cues = [];
  const started = Date.now();
  const say = async (text, holdMs) => {
    const now = Date.now() - started;
    if (cues.length) cues[cues.length - 1].end = now;
    cues.push({ start: now, end: now + 4000, text });
    await page
      .evaluate(([caption, tag]) => {
        sessionStorage.setItem("__caption", caption);
        sessionStorage.setItem("__role", tag);
      }, [text, label])
      .catch(() => undefined);
    await page.waitForTimeout(holdMs ?? Math.min(4200, 1400 + text.split(" ").length * 140));
  };
  const finish = async () => {
    if (cues.length) cues[cues.length - 1].end = Date.now() - started;
    await page.waitForTimeout(800);
    const video = page.video();
    await context.close();
    const target = path.join(OUT, `${role}.webm`);
    if (video) await video.saveAs(target);
    fs.writeFileSync(
      path.join(OUT, `${role}.srt`),
      cues.map((cue, index) => `${index + 1}\n${srtTime(cue.start)} --> ${srtTime(cue.end)}\n${cue.text}\n`).join("\n"),
    );
  };
  return { context, page, say, finish };
}

async function highlight(locator) {
  await locator.scrollIntoViewIfNeeded().catch(() => undefined);
  await locator
    .evaluate((element) => {
      element.style.transition = "box-shadow .2s";
      element.style.boxShadow = "0 0 0 4px rgba(13,138,119,.55)";
      setTimeout(() => (element.style.boxShadow = ""), 900);
    })
    .catch(() => undefined);
  await locator.page().waitForTimeout(450);
}

async function click(locator) {
  await highlight(locator);
  await locator.click();
}

/** A sidebar navigation link (page bodies reuse the same words for shortcuts). */
function nav(page, name) {
  return page.locator("nav.nav").getByRole("link", { name, exact: true });
}

async function login(page, email, password = PASSWORD) {
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="email"]', email);
  await page.waitForTimeout(250);
  await page.fill('input[type="password"]', password);
  await click(page.locator('button[type="submit"]'));
}

async function scrollThrough(page, steps = 3, pause = 900) {
  for (let i = 0; i < steps; i += 1) {
    await page.mouse.wheel(0, 420);
    await page.waitForTimeout(pause);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await page.waitForTimeout(500);
}

// --- Teacher ------------------------------------------------------------------
async function teacher(browser) {
  const { page, say, finish } = await startRole(browser, "2-teacher", "TEACHER · Prof. Arjun Nair");
  await page.goto(`${BASE}/login`);
  await say("Teacher walkthrough. Prof. Arjun Nair teaches Database Management Systems and Operating Systems.");
  await login(page, "teacher@praxis.demo");
  await page.waitForURL("**/teacher");
  await say("The faculty overview: practice sets, attempts, active students and secured papers.");

  // Syllabus library upload + search.
  await click(nav(page, "Syllabus library"));
  await page.waitForSelector(".upload-card");
  await say("Syllabus library. Teachers upload PDF, DOCX or TXT material for their own subjects only.");
  await page.setInputFiles(".upload-card input[type=file]", path.join(FIXTURES, "dbms-transactions-notes.pdf"));
  await page.waitForSelector("text=dbms-transactions-notes.pdf", { timeout: 20000 });
  check("teacher uploads a syllabus PDF in the UI", true);
  await say("The PDF is validated, text-extracted, chunked and embedded for retrieval.");
  const search = page.locator('input[placeholder^="e.g. lossless"]');
  await search.fill("two-phase locking strict");
  await click(page.getByRole("button", { name: "Search" }));
  await page.waitForTimeout(1200);
  await say("Retrieval search shows which syllabus passages will ground generated questions.");

  // Practice set: create, generate, edit, publish.
  await click(nav(page, "Practice studio"));
  await page.waitForSelector(".data-table, .empty-state");
  await say("Practice studio lists the teacher's own sets plus co-teachers' sets in the same subjects, read-only.");
  await page.goto(`${BASE}/teacher/practice/new`);
  await page.waitForSelector('input[placeholder="e.g. Normalization checkpoint"]');
  await page.fill('input[placeholder="e.g. Normalization checkpoint"]', "Transactions quick check");
  await page.selectOption("select >> nth=0", { label: "CS501 · Database Management Systems" });
  await page.waitForSelector(".topic-option");
  await click(page.locator(".topic-option", { hasText: "Transactions" }));
  await click(page.locator(".topic-option", { hasText: "SQL" }));
  await say("A new practice set: title, subject and topics.");
  await click(page.locator('button[type="submit"]'));
  await page.waitForURL(/\/teacher\/practice\/[0-9a-f-]{36}$/);
  await say("The set starts as a draft. Questions can be generated from the syllabus or written by hand.");
  await click(page.getByRole("button", { name: "Generate with AI" }));
  await page.waitForSelector(".modal");
  await say("Generation is grounded in the uploaded syllabus for the chosen topics.");
  await click(page.locator(".modal").getByRole("button", { name: "Generate" }));
  await page.waitForSelector(".question-list .review-item", { timeout: 30000 });
  const generated = await page.locator(".question-list .review-item").count();
  check("practice questions generated", generated > 0, `${generated} questions`);
  await say(`${generated} questions generated, each with options, the correct answer and an explanation.`);
  await click(page.getByRole("button", { name: "Edit question 1" }));
  await page.waitForSelector(".modal textarea");
  const stem = page.locator(".modal textarea").first();
  const revised = `${(await stem.inputValue()).replace(/\?$/, "")} (choose one)?`;
  await stem.fill(revised);
  await say("Any draft question can be edited before publishing: wording, options, answer, marks or type.");
  await click(page.locator(".modal").getByRole("button", { name: "Save question" }));
  await page.waitForSelector(`text=${revised}`);
  await click(page.getByRole("button", { name: "Publish" }));
  await page.waitForSelector(".badge >> text=Published");
  check("teacher publishes the practice set", true);
  await say("Published. Only students enrolled in Database Management Systems can see it.");

  // Generated exam: freeze and generate.
  await click(nav(page, "Examinations"));
  await page.waitForSelector(".exam-card");
  await say("Examinations. Each exam is either AI-generated or an uploaded PDF. Colleagues' exams appear read-only.");
  await click(page.locator(".exam-card", { hasText: "DBMS Internal Assessment 2" }).getByText("Open"));
  await page.waitForSelector("text=Freeze blueprint");
  await say("A draft AI-generated exam. Its blueprint fixes marks, topics, difficulty and Bloom levels.");
  await click(page.getByRole("button", { name: "Freeze blueprint" }));
  await page.waitForSelector("text=/Generate sealed paper/");
  await say("Freezing records a SHA-256 hash of the blueprint. Now the paper can be generated.");
  await click(page.getByRole("button", { name: /Generate sealed paper/ }).first());
  await page.waitForSelector("text=/Paper encrypted with AES-256-GCM and stored|Generation failed/", { timeout: 120000 });
  const sealed = await page.locator("text=Paper encrypted with AES-256-GCM and stored").count();
  check("generated exam is sealed", sealed > 0);
  await say("Questions were generated, validated in five levels, assembled, locked and encrypted. The teacher never sees them.");
  await scrollThrough(page, 3);

  // Uploaded exam.
  await page.goto(`${BASE}/teacher/exams/new`);
  await click(page.getByRole("button", { name: /Upload my paper/ }));
  await say("The other route: the teacher uploads a paper they prepared themselves.");
  await page.selectOption("select >> nth=0", { label: "CS501 · Database Management Systems" });
  await page.fill('input[placeholder^="e.g. DBMS"]', "DBMS Class Test (prepared paper)");
  await click(page.getByRole("button", { name: /Create and continue to upload/ }));
  await page.waitForURL(/\/teacher\/exams\/[0-9a-f-]{36}$/);
  await page.setInputFiles('input[type="file"]', path.join(FIXTURES, "dbms-class-test.pdf"));
  await page.waitForSelector("text=dbms-class-test.pdf", { timeout: 20000 });
  check("teacher uploads an exam paper PDF in the UI", true);
  await say("The PDF is encrypted on upload. It can be replaced until 24 hours before the exam, then it is frozen.");
  await scrollThrough(page, 2);

  // Co-teacher read-only view.
  await page.goto(`${BASE}/teacher/exams`);
  await click(page.locator(".exam-card", { hasText: "OS Surprise Quiz" }).getByText("Open"));
  await page.waitForSelector("text=/View only/");
  check("co-teacher exam is view only", true);
  await say("Prof. Kavya Iyer's exam in a shared subject: visible, but only she or the HOD can change it.");
  await finish();
}

// --- Student ------------------------------------------------------------------
async function student(browser) {
  const { page, say, finish } = await startRole(browser, "3-student", "STUDENT · Riya Sharma");
  await page.goto(`${BASE}/login`);
  await say("Student walkthrough. Riya Sharma is enrolled in both subjects.");
  await login(page, "student@praxis.demo");
  await page.waitForURL("**/student");
  await say("The student overview: progress, weak topics and recommendations.");
  await scrollThrough(page, 2);
  await click(nav(page, "Practice"));
  await page.waitForSelector("a[href^='/student/practice/']");
  await say("Only published sets from enrolled subjects are listed, including the one just published.");
  await click(page.locator(".practice-card", { hasText: "Transactions quick check" }).getByRole("link", { name: /Open/ }));
  await page.waitForURL("**/student/practice/*");
  await say("Test details before starting. The timer is enforced by the server.");
  await click(page.getByRole("button", { name: /Start practice|Start another attempt/ }));
  await page.waitForURL("**/student/attempt/*");
  await page.waitForSelector(".option");
  await say("Answers are saved automatically as the student works.");
  await click(page.locator(".option").nth(0));
  await click(page.getByRole("button", { name: /Next/ }));
  await click(page.locator(".option").nth(1));
  await page.waitForSelector("text=/Saved \\d/", { timeout: 10000 });
  await say("Saved to the server. Now the page is reloaded, as if the browser crashed.");
  page.once("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.waitForSelector(".option");
  await page.waitForTimeout(800);
  const restored = await page.locator(".option.selected").count();
  check("autosaved answer restored after reload", restored >= 1, `${restored} selected on question 1`);
  await say("The saved answers come back. Nothing is lost.");
  for (let index = 0; index < 4; index += 1) {
    const next = page.getByRole("button", { name: /Next/ });
    if (!(await next.isEnabled().catch(() => false))) break;
    await click(next);
    if ((await page.locator(".option.selected").count()) === 0) await click(page.locator(".option").nth(index % 3));
  }
  await click(page.getByRole("button", { name: /Review & submit|Submit attempt/ }).first());
  await page.waitForSelector(".modal");
  await say("Submit the attempt.");
  await click(page.locator(".modal").getByRole("button", { name: "Submit final answers" }));
  await page.waitForURL("**/student/results/*", { timeout: 15000 });
  check("student submits and sees the result", true);
  await say("Instant result with topic breakdown, correct answers and explanations.");
  await scrollThrough(page, 4, 1000);
  await click(nav(page, "Analytics"));
  await page.waitForTimeout(1200);
  await say("Learning analytics: mastery by topic and difficulty, trend and recommendations.");
  await scrollThrough(page, 2);
  await click(nav(page, "History"));
  await page.waitForTimeout(1200);
  await say("Every attempt with its score.");
  await page.goto(`${BASE}/hod/releases`);
  await page.waitForURL("**/student", { timeout: 8000 }).catch(() => null);
  check("student cannot open HOD pages", page.url().endsWith("/student"), page.url());
  await say("Trying to open an HOD page sends the student back. The API refuses it too.");
  await finish();
}

// --- HOD ----------------------------------------------------------------------
async function hod(browser) {
  const { page, say, finish } = await startRole(browser, "4-hod", "HOD · Dr. Meera Raghavan");
  const shared = { tempPassword: "", email: "" };
  await page.goto(`${BASE}/login`);
  await say("Head of Department walkthrough. HOD sign-in always requires an authenticator code.");
  await login(page, "hod@praxis.demo");
  await page.waitForSelector("text=Authenticator code required");
  await say("Password accepted; now the one-time code. The demo fills it in from the seeded authenticator.");
  await click(page.getByRole("button", { name: /demo authenticator/i }));
  await page.waitForTimeout(600);
  await click(page.getByRole("button", { name: "Verify and enter" }));
  await page.waitForURL("**/hod");
  await say("The command centre: sealed papers, release windows and security posture.");
  await page.mouse.wheel(0, 900);
  await page.waitForTimeout(800);
  await say("Department analytics per subject and per teacher.");
  await scrollThrough(page, 1);

  // People: CSV import.
  await click(nav(page, "People"));
  await page.waitForSelector(".data-table");
  await say("People. Accounts are created by the HOD only, never self-registered.");
  await page.setInputFiles('input[accept=".csv,text/csv"]', path.join(FIXTURES, "new-students.csv"));
  await page.waitForSelector("text=/account\\(s\\) imported/", { timeout: 20000 });
  check("HOD imports accounts from CSV", true);
  await say("A CSV import creates several accounts at once, each with a one-time password to hand over.");
  shared.email = (await page.locator(".import-results tbody tr").first().locator("td").nth(1).innerText()).trim();
  shared.tempPassword = (await page.locator(".import-results tbody tr").first().locator("code").innerText()).trim();
  await click(page.locator(".modal").getByRole("button", { name: "Done" }));
  await click(page.getByRole("link", { name: "Analytics for Riya Sharma" }));
  await page.waitForSelector("text=/Riya Sharma: performance/");
  check("HOD opens a student's learning analytics", true);
  await say("Any student's learning analytics, one click from People.");
  await scrollThrough(page, 2);
  await click(nav(page, "Practice studio"));
  await click(page.locator("tr", { hasText: "Transactions quick check" }).getByRole("link"));
  await page.waitForSelector(".tabs");
  await click(page.getByRole("button", { name: /^Analytics/ }));
  await page.waitForSelector(".student-row-link");
  await say("The HOD sees every practice set. Cohort analytics per topic and per student.");
  await click(page.locator(".student-row-link").first());
  await page.waitForSelector("text=/Attempt by/");
  check("staff review a student's attempt", true);
  await say("Staff can open any student's attempt in their subjects, answer by answer.");
  await scrollThrough(page, 2);

  // Subjects: enrolment and topics.
  await click(nav(page, "Subjects"));
  await page.waitForSelector(".subject-index-item");
  await say("Subjects: topics, teacher assignments and student enrolment in one place.");
  await click(page.locator(".subject-index-item", { hasText: "Operating Systems" }));
  await page.waitForTimeout(800);
  await click(page.getByRole("button", { name: "Enrol", exact: true }));
  await page.waitForSelector(".modal .checkbox-row");
  await click(page.locator(".modal .checkbox-row", { hasText: shared.email }).locator("input"));
  await say("Enrolling a new student in Operating Systems; they will see its practice tests.");
  await click(page.locator(".modal").getByRole("button", { name: /^Enrol/ }));
  await page.waitForTimeout(1200);

  // Release ceremony.
  await click(nav(page, "Release control"));
  await page.waitForSelector(".release-card");
  await say("Release control. Papers open only inside their window: 24 hours before to 2 hours after the exam.");
  await click(page.locator(".release-card", { hasText: "DBMS Internal Assessment 1" }).getByRole("button", { name: "Manage release" }));
  await page.waitForTimeout(800);
  await say("Step 1: authorise with a reason. This issues a short-lived release token.");
  await click(page.getByRole("button", { name: /Authorize/ }).first());
  await page.waitForTimeout(1200);
  await say("Step 2: decrypt in memory and verify both the ciphertext and plaintext hashes.");
  await click(page.getByRole("button", { name: /Decrypt/ }));
  await page.waitForSelector("text=Integrity verified", { timeout: 15000 });
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 20000 }),
    click(page.getByRole("button", { name: /Generate printable PDF/ })),
  ]);
  const saved = path.join(OUT, "printed-paper.pdf");
  await download.saveAs(saved);
  check("HOD prints the decrypted paper", fs.readFileSync(saved).subarray(0, 5).toString() === "%PDF-");
  await say("Step 3: a controlled print. At most three numbered copies per paper, each one audited.");
  await click(page.getByRole("button", { name: "Mark released" }));
  await page.waitForTimeout(1200);
  await say("Step 4: mark the paper released to invigilation. The lifecycle is closed.");
  await page.locator("text=Release history").scrollIntoViewIfNeeded();
  await page.waitForTimeout(600);
  await say("The release history keeps every authorisation, decryption and print.");

  // Audit and security.
  await click(nav(page, "Audit trail"));
  await page.waitForSelector(".data-table");
  await click(page.getByRole("button", { name: "Verify hash chain" }));
  await page.waitForTimeout(1500);
  await say("Every action is in a hash-chained audit log. Verification re-walks the chain to detect tampering.");
  await click(nav(page, "Security events"));
  await page.waitForSelector(".security-event");
  await say("Security events: decryptions, prints, lockouts and other anomalies, which the HOD can resolve.");
  const resolve = page.getByRole("button", { name: "Mark resolved" }).first();
  if (await resolve.count()) await click(resolve);
  await page.waitForTimeout(1000);
  await finish();
  return shared;
}

// --- First sign-in ------------------------------------------------------------
async function firstLogin(browser, shared) {
  const { page, say, finish } = await startRole(browser, "5-first-sign-in", "NEW STUDENT · first sign-in");
  await page.goto(`${BASE}/login`);
  await say(`First sign-in for ${shared.email}, imported by the HOD with a one-time password.`);
  await login(page, shared.email, shared.tempPassword);
  await page.waitForURL("**/account");
  check("temporary password forces a password change", true);
  await say("Until the temporary password is replaced, only the Account page is available, in the UI and in the API.");
  const inputs = page.locator('input[type="password"]');
  await inputs.nth(0).fill(shared.tempPassword);
  await inputs.nth(1).fill("Fresh#Start2026");
  await inputs.nth(2).fill("Fresh#Start2026");
  await click(page.getByRole("button", { name: "Update password" }));
  await page.waitForURL("**/student", { timeout: 15000 });
  check("new student reaches the dashboard after changing password", true);
  await say("Done. The student lands in their workspace with the subjects the HOD enrolled them in.");
  await click(nav(page, "Practice"));
  await page.waitForTimeout(1500);
  await say("Their practice list shows only their enrolled subjects.");
  await finish();
}

// --- Intro card -----------------------------------------------------------------
async function intro(browser) {
  const { page, say, finish } = await startRole(browser, "1-intro", "PRAXIS · walkthrough");
  await page.goto(`${BASE}/login`);
  await say("Praxis: secure assessment for an academic department. Three roles: student, teacher and Head of Department.", 4500);
  await say("Teachers build practice tests and examinations; papers stay encrypted until the HOD releases them.", 4500);
  await say("This recording drives the real application end to end, one role at a time.", 3500);
  await finish();
}

(async () => {
  const browser = await chromium.launch();
  try {
    await intro(browser);
    await teacher(browser);
    await student(browser);
    const shared = await hod(browser);
    await firstLogin(browser, shared);
  } catch (error) {
    problems.push(`ERROR: ${error.stack || error}`);
  } finally {
    await browser.close();
  }
  const summary = `${results.join("\n")}\n\n${problems.length} problem(s)${problems.length ? `:\n${problems.join("\n")}` : ""}`;
  fs.writeFileSync(path.join(OUT, "results.txt"), summary);
  console.log(summary);
  process.exit(problems.length || results.some((line) => line.startsWith("FAIL")) ? 1 : 0);
})();
