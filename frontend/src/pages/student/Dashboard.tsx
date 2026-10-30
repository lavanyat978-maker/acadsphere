import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpenCheck,
  CalendarClock,
  Flame,
  Gauge,
  Lightbulb,
  Target,
  TrendingUp,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useApi } from "../../lib/useApi";
import type { PracticeTest, StudentDashboard } from "../../lib/types";
import { formatDateTime, relativeTime, scoreTone, firstName as firstNameOf } from "../../lib/format";
import { Badge, Button, Card, EmptyState, ErrorState, PageLoading, PageHeader, Progress, Stat } from "../../components/ui";
import { ScoreRing, TrendChart, TopicBars } from "../../components/charts";

export function StudentDashboard() {
  const { user } = useAuth();
  const dashboard = useApi<StudentDashboard>("/analytics/dashboard");
  const tests = useApi<PracticeTest[]>("/practice/tests");

  if (dashboard.loading && !dashboard.data) return <PageLoading label="Preparing your learning brief" />;
  if (dashboard.error) return <ErrorState message={dashboard.error} onRetry={dashboard.reload} />;
  if (!dashboard.data) return null;

  const data = dashboard.data;
  const summary = data.summary;
  const recommended = (tests.data ?? []).filter((test) => test.attempt_count === 0).slice(0, 3);
  const firstName = firstNameOf(user?.full_name, "there");

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Student workspace"
        title={`Good to see you, ${firstName}.`}
        description="Your practice performance, weak topics and the next best action."
        actions={
          <Link to="/student/practice">
            <Button icon={<BookOpenCheck size={16} />}>Browse practice</Button>
          </Link>
        }
      />

      <div className="hero-grid">
        <Card className="hero-card">
          <div className="hero-copy">
            <div className="eyebrow">Learning signal</div>
            <h2>{summary.total_attempts ? "You are building a measurable edge." : "Your first attempt sets the baseline."}</h2>
            <p>
              {summary.total_attempts
                ? `Across ${summary.total_attempts} submitted attempt${summary.total_attempts === 1 ? "" : "s"}, your accuracy is ${summary.overall_accuracy.toFixed(1)}%.`
                : "Pick a practice test. Every answer feeds topic-level analytics and recommendations."}
            </p>
            <div className="hero-actions">
              <Link to="/student/analytics">
                <Button variant="secondary" icon={<TrendingUp size={16} />}>Open analytics</Button>
              </Link>
              <Link to="/student/history">
                <Button variant="ghost">Review attempts</Button>
              </Link>
            </div>
          </div>
          <ScoreRing percentage={summary.average_percentage} label="average" />
        </Card>

        {data.enrolled_subjects === 0 && (
          <div className="notice notice-warning">
            You are not enrolled in any subject yet, so no practice tests are available. Ask the Head of Department to enrol you.
          </div>
        )}

        <div className="stat-grid stat-grid-tight">
          <Stat
            label="Tests available"
            value={data.tests_available}
            hint={`${data.tests_attempted} attempted so far`}
            icon={<BookOpenCheck size={18} />}
            tone="accent"
          />
          <Stat
            label="Best score"
            value={`${summary.best_percentage.toFixed(0)}%`}
            hint={summary.improvement >= 0 ? `+${summary.improvement.toFixed(1)} pts recent` : `${summary.improvement.toFixed(1)} pts recent`}
            icon={<Target size={18} />}
            tone={summary.best_percentage >= 75 ? "success" : "default"}
          />
          <Stat
            label="Accuracy"
            value={`${summary.overall_accuracy.toFixed(0)}%`}
            hint="Across all answered questions"
            icon={<Gauge size={18} />}
          />
          <Stat
            label="Momentum"
            value={summary.improvement >= 0 ? "Rising" : "Needs focus"}
            hint="Last three versus previous three"
            icon={<Flame size={18} />}
            tone={summary.improvement >= 0 ? "success" : "warning"}
          />
        </div>
      </div>

      <div className="split-grid">
        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Trajectory</div>
              <h3>Score trend</h3>
            </div>
            <Badge tone="neutral">Last 10 attempts</Badge>
          </div>
          <TrendChart
            points={data.trend.map((row) => ({ percentage: Number(row.percentage ?? 0) }))}
          />
          <div className="trend-caption">
            <span>Earlier</span>
            <span>Most recent</span>
          </div>
        </Card>

        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Focus areas</div>
              <h3>Weakest topics</h3>
            </div>
            <Link to="/student/analytics" className="text-link">View all</Link>
          </div>
          {data.weak_topics.length ? (
            <TopicBars
              items={data.weak_topics.map((topic) => ({
                label: topic.topic_name,
                percentage: topic.accuracy,
                detail: `${topic.correct}/${topic.attempted}`,
              }))}
            />
          ) : (
            <EmptyState
              icon={<Target size={20} />}
              title="No weak topic detected"
              description="Attempt more questions to expose topic-level gaps."
            />
          )}
        </Card>
      </div>

      <div className="split-grid split-grid-wide">
        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Recommended next</div>
              <h3>Practice assigned to your gaps</h3>
            </div>
          </div>
          {data.recommendations.length ? (
            <div className="recommendation-list">
              {data.recommendations.slice(0, 2).map((raw, index) => {
                const rec = raw as {
                  title: string;
                  topic_name: string;
                  action: string;
                  priority: string;
                  practice_tests: Array<{ id: string; title: string; question_count: number; duration_minutes: number; completed: boolean }>;
                };
                return (
                  <div className="recommendation" key={index}>
                    <div className="recommendation-icon"><Lightbulb size={17} /></div>
                    <div>
                      <div className="recommendation-title">
                        {rec.title}
                        <Badge tone={rec.priority === "HIGH" ? "danger" : "warning"}>{rec.priority}</Badge>
                      </div>
                      <p>{rec.action}</p>
                      <div className="recommendation-links">
                        {rec.practice_tests.map((test) => (
                          <Link key={test.id} className="mini-link" to={`/student/practice/${test.id}`}>
                            {test.title} · {test.question_count} Q <ArrowRight size={13} />
                          </Link>
                        ))}
                        {!rec.practice_tests.length && (
                          <Link className="mini-link" to="/student/practice">
                            Open practice library <ArrowRight size={13} />
                          </Link>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState icon={<Lightbulb size={20} />} title="Recommendations appear after an attempt" />
          )}

          {recommended.length > 0 && (
            <div className="next-tests">
              <div className="mini-heading">Unattempted tests</div>
              {recommended.map((test) => (
                <Link className="next-test" key={test.id} to={`/student/practice/${test.id}`}>
                  <div>
                    <strong>{test.title}</strong>
                    <span>{test.subject_name} · {test.question_count} questions · {test.duration_minutes} min</span>
                  </div>
                  <Badge tone="accent">{test.total_marks} marks</Badge>
                </Link>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Recent activity</div>
              <h3>Latest attempts</h3>
            </div>
          </div>
          {data.recent_attempts.length ? (
            <div className="activity-list">
              {data.recent_attempts.map((attempt) => (
                <Link
                  key={attempt.attempt_id}
                  to={attempt.status === "IN_PROGRESS" ? `/student/attempt/${attempt.attempt_id}` : `/student/results/${attempt.attempt_id}`}
                  className="activity-row"
                >
                  <div className="activity-main">
                    <strong>{attempt.test_title}</strong>
                    <span>{attempt.subject_name} · {relativeTime(attempt.submitted_at ?? attempt.started_at)}</span>
                  </div>
                  <div className="activity-score">
                    <Badge tone={scoreTone(attempt.percentage)}>{attempt.percentage.toFixed(0)}%</Badge>
                    <span>{attempt.status.replace(/_/g, " ").toLowerCase()}</span>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<CalendarClock size={20} />}
              title="No attempts yet"
              description="Your first practice attempt will appear here."
            />
          )}
          <div className="progress-foot">
            <Progress
              value={data.tests_attempted ? (data.tests_attempted / Math.max(1, data.tests_available + data.tests_attempted)) * 100 : 0}
              label="Practice coverage"
            />
            <span className="muted">Last updated {formatDateTime(new Date().toISOString())}</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
