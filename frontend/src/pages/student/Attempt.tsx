import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Clock3,
  CloudOff,
  Flag,
  Send,
  ShieldCheck,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { Attempt, PracticeResult } from "../../lib/types";
import { Badge, Button, Card, ErrorState, Modal, PageLoading, Progress, Spinner } from "../../components/ui";
import { secondsToClock, titleCase } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

export function AttemptPage() {
  const { attemptId = "" } = useParams();
  const attempt = useApi<Attempt>(`/practice/attempts/${attemptId}`);
  const navigate = useNavigate();
  const { toast } = useToast();

  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [current, setCurrent] = useState(0);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submitted = useRef(false);
  const submitRef = useRef<(auto?: boolean) => void>(() => undefined);
  // Autosave: answers are pushed to the server shortly after every change, so a
  // refresh, crash or lost connection never loses work. The server grades the
  // saved answers if the deadline passes without a submission.
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const dirty = useRef(false);
  const restored = useRef(false);
  const timeSpent = useRef<Record<string, number>>({});
  const shownAt = useRef<number>(Date.now());

  useEffect(() => {
    if (!attempt.data) return;
    if (attempt.data.status !== "IN_PROGRESS") {
      navigate(`/student/results/${attemptId}`, { replace: true });
      return;
    }
    const seconds = Math.max(
      0,
      Math.round((new Date(attempt.data.expires_at).getTime() - new Date(attempt.data.server_time).getTime()) / 1000),
    );
    setRemaining(seconds);
    const timer = window.setInterval(() => {
      setRemaining((value) => (value === null ? null : Math.max(0, value - 1)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [attempt.data?.id, attempt.data?.status, attemptId, navigate]);

  useEffect(() => {
    if (remaining !== 0 || submitted.current) return;
    submitRef.current(true);
  }, [remaining]);

  // Restore autosaved answers once, when the attempt first loads.
  useEffect(() => {
    if (!attempt.data || restored.current) return;
    restored.current = true;
    const saved = attempt.data.saved_answers ?? {};
    const initial: Record<string, string[]> = {};
    for (const [questionId, entry] of Object.entries(saved)) {
      initial[questionId] = entry.selected ?? [];
      timeSpent.current[questionId] = entry.response_time_seconds ?? 0;
    }
    if (Object.keys(initial).length) {
      setAnswers(initial);
      toast(`Restored ${Object.keys(initial).length} saved answer(s).`, "info");
    }
    setSavedAt(attempt.data.last_saved_at);
  }, [attempt.data, toast]);

  const questions = attempt.data?.questions ?? [];
  const question = questions[current];

  // Track time on each question for the response-time analytics.
  const recordTime = useCallback(() => {
    const visible = questions[current];
    if (!visible) return;
    const now = Date.now();
    timeSpent.current[visible.id] = (timeSpent.current[visible.id] ?? 0) + (now - shownAt.current) / 1000;
    shownAt.current = now;
  }, [questions, current]);

  useEffect(() => {
    shownAt.current = Date.now();
    return () => recordTime();
  }, [current, recordTime]);

  const buildAnswers = useCallback(() => {
    recordTime();
    return Object.entries(answers)
      .filter(([, selected]) => selected.length > 0)
      .map(([questionId, selected]) => ({
        question_id: questionId,
        selected,
        response_time_seconds: Math.round(timeSpent.current[questionId] ?? 0),
      }));
  }, [answers, recordTime]);

  const save = useCallback(async () => {
    if (!attempt.data || submitted.current || !dirty.current) return;
    dirty.current = false;
    setSaveState("saving");
    try {
      const result = await api<{ last_saved_at: string }>(`/practice/attempts/${attempt.data.id}/answers`, {
        method: "PUT",
        body: { answers: buildAnswers() },
      });
      setSavedAt(result.last_saved_at);
      setSaveState("saved");
    } catch (err) {
      dirty.current = true;
      setSaveState("error");
      const code = (err as { code?: string }).code;
      if (code === "ATTEMPT_EXPIRED" || code === "ATTEMPT_CLOSED") {
        submitted.current = true;
        toast("Time is up. Your saved answers were submitted.", "info");
        navigate(`/student/results/${attemptId}`, { replace: true });
      }
    }
  }, [attempt.data, attemptId, buildAnswers, navigate, toast]);

  useEffect(() => {
    if (!restored.current) return;
    dirty.current = true;
    const handle = window.setTimeout(() => void save(), 1200);
    return () => window.clearTimeout(handle);
  }, [answers, save]);

  // Retry a failed save, and save on a slow cadence so response times stay current.
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (dirty.current || saveState === "error") void save();
    }, 15000);
    return () => window.clearInterval(handle);
  }, [save, saveState]);
  const answeredCount = Object.values(answers).filter((selected) => selected.length > 0).length;

  const submit = useCallback(
    async (auto = false) => {
      if (!attempt.data || submitted.current) return;
      submitted.current = true;
      setSubmitting(true);
      setConfirmOpen(false);
      const payload = { answers: buildAnswers() };
      try {
        const result = await api<PracticeResult>(`/practice/attempts/${attempt.data.id}/submit`, {
          method: "POST",
          body: payload,
        });
        toast(auto ? "Time expired — your answers were submitted." : "Attempt submitted.", auto ? "info" : "success");
        navigate(`/student/results/${result.attempt_id}`, { replace: true });
      } catch (err) {
        submitted.current = false;
        setSubmitting(false);
        toast(errorMessage(err), "error");
      }
    },
    [attempt.data, buildAnswers, navigate, toast],
  );
  submitRef.current = submit;

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (!submitted.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);

  const select = (optionId: string) => {
    if (!question) return;
    const multiple = question.question_type === "MULTI_CHOICE";
    setAnswers((currentAnswers) => {
      const existing = currentAnswers[question.id] ?? [];
      const next = multiple
        ? existing.includes(optionId)
          ? existing.filter((id) => id !== optionId)
          : [...existing, optionId]
        : [optionId];
      return { ...currentAnswers, [question.id]: next };
    });
  };

  const optionState = (optionId: string) => (answers[question?.id ?? ""] ?? []).includes(optionId);

  const navigator = useMemo(
    () =>
      questions.map((item, index) => ({
        index,
        answered: (answers[item.id] ?? []).length > 0,
        flagged: false,
      })),
    [questions, answers],
  );

  if (attempt.loading && !attempt.data) return <PageLoading label="Preparing your attempt" />;
  if (attempt.error) return <ErrorState message={attempt.error} onRetry={attempt.reload} />;
  if (!attempt.data || !question) return <Spinner label="Loading questions" />;

  const criticalTime = remaining !== null && remaining <= 60;

  return (
    <div className="attempt-page">
      <div className="attempt-topbar">
        <div className="attempt-title">
          <span className="eyebrow">{attempt.data.test_title}</span>
          <div className="attempt-flags">
            <Badge tone="neutral">{questions.length} questions</Badge>
            <Badge tone="neutral">{attempt.data.total_marks} marks</Badge>
            <span className="server-note"><ShieldCheck size={13} /> Server-timed</span>
            <span className="autosave-status" aria-live="polite">
              {saveState === "saving" && "Saving…"}
              {saveState === "error" && <><CloudOff size={13} /> Not saved — retrying</>}
              {(saveState === "saved" || saveState === "idle") && savedAt && <>Saved {new Date(savedAt).toLocaleTimeString()}</>}
            </span>
          </div>
        </div>
        <div className={`timer ${criticalTime ? "timer-critical" : ""}`}>
          <Clock3 size={18} />
          <div>
            <span>Time remaining</span>
            <strong>{remaining === null ? "—" : secondsToClock(remaining)}</strong>
          </div>
        </div>
      </div>

      <div className="attempt-grid">
        <Card className="question-card">
          <div className="question-head">
            <div className="question-count">
              Question {current + 1} <span>of {questions.length}</span>
            </div>
            <div className="question-tags">
              <Badge tone="neutral">{titleCase(question.difficulty)}</Badge>
              <Badge tone="neutral">{question.topic_name}</Badge>
              <Badge tone="accent">{question.marks} mark{question.marks === 1 ? "" : "s"}</Badge>
            </div>
          </div>

          <h2 className="question-text">{question.question_text}</h2>
          <p className="question-instruction">
            {question.question_type === "MULTI_CHOICE"
              ? "Select all options that apply."
              : "Select the single best answer."}
          </p>

          <div className="option-list" role={question.question_type === "MULTI_CHOICE" ? "group" : "radiogroup"}>
            {question.options.map((option, index) => {
              const selected = optionState(option.id);
              return (
                <button
                  key={option.id}
                  type="button"
                  className={`option ${selected ? "selected" : ""}`}
                  onClick={() => select(option.id)}
                  role={question.question_type === "MULTI_CHOICE" ? "checkbox" : "radio"}
                  aria-checked={selected}
                >
                  <span className="option-key">{String.fromCharCode(65 + index)}</span>
                  <span className="option-text">{option.text}</span>
                  {selected && <CheckCircle2 size={18} className="option-check" />}
                </button>
              );
            })}
          </div>

          <div className="question-footer">
            <Button
              variant="secondary"
              onClick={() => setCurrent((value) => Math.max(0, value - 1))}
              disabled={current === 0}
              icon={<ArrowLeft size={15} />}
            >
              Previous
            </Button>
            <div className="question-progress">
              <Progress value={(answeredCount / questions.length) * 100} />
              <span>{answeredCount} of {questions.length} answered</span>
            </div>
            {current < questions.length - 1 ? (
              <Button onClick={() => setCurrent((value) => Math.min(questions.length - 1, value + 1))}>
                Next <ArrowRight size={15} />
              </Button>
            ) : (
              <Button variant="primary" onClick={() => setConfirmOpen(true)} icon={<Send size={15} />}>
                Review & submit
              </Button>
            )}
          </div>
        </Card>

        <aside className="attempt-side">
          <Card>
            <div className="card-head compact">
              <div><div className="eyebrow">Navigator</div><h3>Questions</h3></div>
              {criticalTime && <Badge tone="danger" dot>Time low</Badge>}
            </div>
            <div className="question-grid">
              {navigator.map((item) => (
                <button
                  key={item.index}
                  type="button"
                  className={`qnav ${item.answered ? "answered" : ""} ${item.index === current ? "current" : ""}`}
                  onClick={() => setCurrent(item.index)}
                  aria-label={`Question ${item.index + 1}${item.answered ? ", answered" : ""}`}
                >
                  {item.index + 1}
                </button>
              ))}
            </div>
            <div className="navigator-legend">
              <span><i className="legend-dot current" /> Current</span>
              <span><i className="legend-dot answered" /> Answered</span>
              <span><i className="legend-dot" /> Open</span>
            </div>
            <Button
              variant="soft"
              className="submit-side"
              onClick={() => setConfirmOpen(true)}
              icon={<Flag size={15} />}
            >
              Submit attempt
            </Button>
          </Card>

          <Card className="attempt-warning">
            <AlertTriangle size={17} />
            <p>Do not close the tab. The server enforces the deadline even if this page is interrupted.</p>
          </Card>
        </aside>
      </div>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Submit this attempt?"
        description={`${answeredCount} of ${questions.length} questions answered. Unanswered questions score zero.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>Keep working</Button>
            <Button loading={submitting} onClick={() => submit(false)} icon={<Send size={15} />}>
              Submit final answers
            </Button>
          </>
        }
      >
        <div className="submit-summary">
          <div><span>Answered</span><strong>{answeredCount}</strong></div>
          <div><span>Unanswered</span><strong>{questions.length - answeredCount}</strong></div>
          <div><span>Time left</span><strong>{remaining === null ? "—" : secondsToClock(remaining)}</strong></div>
        </div>
      </Modal>
    </div>
  );
}
