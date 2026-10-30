import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  Info,
  Play,
  ShieldCheck,
  Target,
  Timer,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { Attempt, PracticeTestDetail } from "../../lib/types";
import { Badge, Button, Card, ErrorState, KeyValue, PageHeader, PageLoading } from "../../components/ui";
import { difficultyTone, scoreTone, formatDateTime } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

export function PracticeDetail() {
  const { testId = "" } = useParams();
  const test = useApi<PracticeTestDetail>(`/practice/tests/${testId}`);
  const navigate = useNavigate();
  const { toast } = useToast();
  const [starting, setStarting] = useState(false);

  if (test.loading && !test.data) return <PageLoading label="Opening practice set" />;
  if (test.error) return <ErrorState message={test.error} onRetry={test.reload} />;
  if (!test.data) return null;
  const data = test.data;

  const start = async () => {
    setStarting(true);
    try {
      const attempt = await api<Attempt>(`/practice/tests/${data.id}/attempts`, { method: "POST" });
      navigate(`/student/attempt/${attempt.id}`);
    } catch (err) {
      toast(errorMessage(err), "error");
      setStarting(false);
    }
  };

  return (
    <div className="stack">
      <Link to="/student/practice" className="back-link">
        <ArrowLeft size={15} /> Practice library
      </Link>
      <PageHeader
        eyebrow={data.subject_name}
        title={data.title}
        description={data.description || "A focused practice set with instant scoring and explanations."}
        actions={<Badge tone={difficultyTone(data.difficulty)}>{data.difficulty.toLowerCase()}</Badge>}
      />

      <div className="briefing-grid">
        <Card className="briefing-main">
          <div className="briefing-topics">
            <div className="eyebrow">Topics in this set</div>
            <div className="topic-tags">
              {data.topics.map((topic) => (
                <span key={topic} className="tag">{topic}</span>
              ))}
            </div>
          </div>

          <div className="briefing-stats">
            <div><BookOpenCheck size={18} /><strong>{data.question_count}</strong><span>questions</span></div>
            <div><Clock3 size={18} /><strong>{data.duration_minutes}</strong><span>minutes</span></div>
            <div><Target size={18} /><strong>{data.total_marks}</strong><span>marks</span></div>
          </div>

          <div className="instructions-panel">
            <div className="instructions-head"><Info size={16} /> Before you begin</div>
            <ul>
              <li>The timer is enforced on the server. Answers are auto-submitted when time expires.</li>
              <li>You can move between questions and change answers until submission.</li>
              <li>Correct answers and explanations unlock after submission.</li>
              <li>Attempts are private. Faculty see aggregate analytics, not your individual answers.</li>
            </ul>
          </div>

          {data.availability !== "OPEN" || data.attempts_remaining === 0 ? (
            <div className="notice notice-warning">
              {data.availability === "UPCOMING"
                ? `This set opens ${formatDateTime(data.opens_at)}.`
                : data.availability === "CLOSED"
                  ? `This set closed ${formatDateTime(data.closes_at)}. Your past results stay available in History.`
                  : `You have used all ${data.max_attempts} attempt(s) for this set.`}
            </div>
          ) : (
            (data.closes_at || data.max_attempts) && (
              <div className="notice">
                {[
                  data.closes_at ? `Closes ${formatDateTime(data.closes_at)}; an attempt in progress ends then.` : "",
                  data.attempts_remaining !== null ? `${data.attempts_remaining} attempt(s) remaining.` : "",
                ].filter(Boolean).join(" ")}
              </div>
            )
          )}

          <div className="briefing-actions">
            <Button
              size="lg"
              loading={starting}
              icon={<Play size={17} />}
              onClick={start}
              disabled={data.availability !== "OPEN" || data.attempts_remaining === 0}
            >
              {data.attempt_count > 0 ? "Start another attempt" : "Start practice"}
            </Button>
            <Link to="/student/analytics">
              <Button variant="ghost">View analytics</Button>
            </Link>
          </div>
        </Card>

        <div className="stack">
          <Card>
            <div className="card-head">
              <div><div className="eyebrow">Your history</div><h3>On this set</h3></div>
              <ShieldCheck size={17} className="muted" />
            </div>
            <KeyValue
              items={[
                { label: "Attempts", value: data.attempt_count },
                {
                  label: "Best score",
                  value:
                    data.best_score !== null ? (
                      <Badge tone={scoreTone(data.best_score)}>{data.best_score.toFixed(1)}%</Badge>
                    ) : (
                      "Not attempted"
                    ),
                },
                { label: "Last attempt", value: data.last_attempt_at ? new Date(data.last_attempt_at).toLocaleDateString() : "—" },
                { label: "Status", value: <Badge tone="success">Published</Badge> },
              ]}
            />
          </Card>

          <Card className="security-card">
            <div className="security-icon"><Timer size={19} /></div>
            <div>
              <strong>Server-authoritative timing</strong>
              <p>Deadlines come from the API, not your device clock. Late submissions inside the grace window are flagged.</p>
            </div>
          </Card>

          <Card>
            <div className="card-head">
              <div><div className="eyebrow">Scoring</div><h3>How marks work</h3></div>
            </div>
            <div className="scoring-list">
              <div><CheckCircle2 size={15} /> Correct choices earn full question marks.</div>
              <div><CheckCircle2 size={15} /> Accuracy counts correct answers, not skipped questions.</div>
              <div><CheckCircle2 size={15} /> Topic breakdowns drive recommendations.</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
