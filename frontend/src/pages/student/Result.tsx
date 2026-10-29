import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Target,
  XCircle,
} from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { PracticeResult } from "../../lib/types";
import { Badge, Card, ErrorState, PageHeader, PageLoading, Progress, Stat } from "../../components/ui";
import { ScoreRing, TopicBars } from "../../components/charts";
import { formatDateTime, scoreTone, titleCase } from "../../lib/format";

export function Result() {
  const { attemptId = "" } = useParams();
  const result = useApi<PracticeResult>(`/practice/attempts/${attemptId}/result`);

  if (result.loading && !result.data) return <PageLoading label="Scoring your attempt" />;
  if (result.error) return <ErrorState message={result.error} onRetry={result.reload} />;
  if (!result.data) return null;
  const data = result.data;

  const topicMap = new Map<string, { attempted: number; correct: number }>();
  for (const item of data.review) {
    const bucket = topicMap.get(item.topic_name) ?? { attempted: 0, correct: 0 };
    bucket.attempted += 1;
    if (item.is_correct) bucket.correct += 1;
    topicMap.set(item.topic_name, bucket);
  }
  const tone = scoreTone(data.percentage);

  return (
    <div className="stack">
      <Link to="/student/practice" className="back-link">
        <ArrowLeft size={15} /> Practice library
      </Link>
      <PageHeader
        eyebrow="Attempt result"
        title={data.test_title}
        description={`${data.subject_name} · submitted ${formatDateTime(data.created_at)}`}
        actions={
          <div className="page-actions-inline">
            <Link to={`/student/practice/${data.test_id}`}>
              <Badge tone="neutral"><RefreshCw size={12} /> Retry set</Badge>
            </Link>
            <Link to="/student/analytics">
              <Badge tone="accent">Analytics <ArrowRight size={12} /></Badge>
            </Link>
          </div>
        }
      />

      <div className="result-hero">
        <Card className="result-score-card">
          <ScoreRing percentage={data.percentage} size={150} label="final score" />
          <div className="result-score-copy">
            <Badge tone={tone}>{data.score} / {data.max_score} marks</Badge>
            <h2>
              {data.percentage >= 75
                ? "Strong command of this set."
                : data.percentage >= 50
                  ? "Solid base — targeted practice will close the gap."
                  : "This topic needs another pass."}
            </h2>
            <p>
              Accuracy {data.accuracy.toFixed(1)}% · average {data.avg_response_time.toFixed(0)}s per question.
              Explanations are unlocked below.
            </p>
          </div>
        </Card>
        <div className="stat-grid stat-grid-tight">
          <Stat label="Correct" value={data.review.filter((item) => item.is_correct).length} hint={`of ${data.review.length} questions`} icon={<CheckCircle2 size={17} />} tone="success" />
          <Stat label="Accuracy" value={`${data.accuracy.toFixed(0)}%`} hint="Correct ÷ answered" icon={<Target size={17} />} />
          <Stat label="Avg. response" value={`${data.avg_response_time.toFixed(0)}s`} hint="Per question" icon={<Clock3 size={17} />} />
          <Stat label="Outcome" value={`${data.percentage.toFixed(0)}%`} hint={data.percentage >= 50 ? "Passing range" : "Below target"} tone={tone} />
        </div>
      </div>

      <div className="split-grid">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Topic mastery</div><h3>Where marks came from</h3></div>
          </div>
          <TopicBars
            items={Array.from(topicMap.entries()).map(([name, bucket]) => ({
              label: name,
              percentage: (bucket.correct / bucket.attempted) * 100,
              detail: `${bucket.correct}/${bucket.attempted}`,
            }))}
          />
        </Card>
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Scoring detail</div><h3>Attempt facts</h3></div>
          </div>
          <div className="result-facts">
            <div><span>Raw score</span><strong>{data.score} / {data.max_score}</strong></div>
            <div><span>Percentage</span><strong>{data.percentage.toFixed(2)}%</strong></div>
            <div><span>Accuracy</span><strong>{data.accuracy.toFixed(2)}%</strong></div>
            <div><span>Submitted</span><strong>{formatDateTime(data.created_at)}</strong></div>
          </div>
          <div className="progress-foot">
            <Progress
              value={data.percentage}
              tone={tone === "success" ? "success" : tone === "warning" ? "warning" : "danger"}
              label="Score"
            />
          </div>
        </Card>
      </div>

      <Card>
        <div className="card-head">
          <div><div className="eyebrow">Answer review</div><h3>Question-by-question</h3></div>
          <Badge tone="neutral">{data.review.length} questions</Badge>
        </div>
        <div className="review-list">
          {data.review.map((item, index) => (
            <div className={`review-item ${item.is_correct ? "correct" : "incorrect"}`} key={item.question_id}>
              <div className="review-head">
                <span className="review-number">{index + 1}</span>
                <div className="review-title">
                  <strong>{item.question_text}</strong>
                  <div className="review-tags">
                    <Badge tone="neutral">{item.topic_name}</Badge>
                    <Badge tone="neutral">{titleCase(item.difficulty)}</Badge>
                    <span className="muted">{item.response_time_seconds.toFixed(0)}s</span>
                  </div>
                </div>
                <Badge tone={item.is_correct ? "success" : "danger"}>
                  {item.is_correct ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                  {item.awarded_marks} / {item.marks}
                </Badge>
              </div>
              <div className="review-options">
                {item.options.map((option) => {
                  const chosen = item.selected.includes(option.id);
                  const correct = item.correct_answers.includes(option.id);
                  return (
                    <div
                      key={option.id}
                      className={`review-option ${chosen ? "chosen" : ""} ${correct ? "correct-answer" : ""}`}
                    >
                      <span className="option-key">{option.id.toUpperCase()}</span>
                      <span>{option.text}</span>
                      {correct && <CheckCircle2 size={15} />}
                      {chosen && !correct && <XCircle size={15} />}
                      {chosen && <span className="chosen-flag">your answer</span>}
                    </div>
                  );
                })}
              </div>
              {item.explanation && (
                <div className="explanation">
                  <strong>Why</strong>
                  <p>{item.explanation}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
