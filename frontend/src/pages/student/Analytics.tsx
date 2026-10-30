import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Lightbulb, Target, TrendingUp } from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { StudentAnalytics } from "../../lib/types";
import { Badge, Card, EmptyState, ErrorState, PageHeader, PageLoading, Progress, Stat } from "../../components/ui";
import { ScoreRing, TrendChart, TopicBars } from "../../components/charts";
import { scoreTone, titleCase } from "../../lib/format";

/** Learning analytics for the signed-in student, or (studentId) any student for the HOD. */
export function Analytics({ studentId }: { studentId?: string } = {}) {
  const params = useParams();
  const viewedId = studentId ?? params.userId;
  const analytics = useApi<StudentAnalytics & { student?: { full_name: string; email: string } }>(
    viewedId ? `/analytics/student/${viewedId}` : "/practice/analytics/me",
  );

  if (analytics.loading && !analytics.data) return <PageLoading label="Crunching your learning signal" />;
  if (analytics.error) return <ErrorState message={analytics.error} onRetry={analytics.reload} />;
  if (!analytics.data) return null;
  const data = analytics.data;

  const difficulties = data.difficulty_performance as Array<{
    difficulty: string;
    attempted: number;
    correct: number;
    accuracy: number;
    avg_time: number;
  }>;

  return (
    <div className="stack">
      {viewedId && (
        <Link to="/hod/people" className="back-link">
          <ArrowLeft size={15} /> People
        </Link>
      )}
      <PageHeader
        eyebrow="Learning analytics"
        title={viewedId ? `${data.student?.full_name ?? "Student"}: performance` : "Your performance, decomposed"}
        description={
          viewedId
            ? `${data.student?.email ?? ""} · topic mastery, difficulty response and the recommendations this student sees.`
            : "Topic-level mastery, difficulty response and the recommendations generated from your attempts."
        }
      />

      <div className="analytics-top">
        <Card className="analytics-score">
          <ScoreRing percentage={data.average_percentage} label="average" size={140} />
          <div>
            <Badge tone={scoreTone(data.average_percentage)}>
              {data.average_percentage >= 75 ? "Strong" : data.average_percentage >= 50 ? "Developing" : "Needs focus"}
            </Badge>
            <h2>{data.total_attempts} attempts analysed</h2>
            <p>
              Best score {data.best_percentage.toFixed(1)}% · accuracy {data.overall_accuracy.toFixed(1)}% ·
              {" "}{data.improvement >= 0 ? "up" : "down"} {Math.abs(data.improvement).toFixed(1)} points recently.
            </p>
          </div>
        </Card>
        <div className="stat-grid stat-grid-tight">
          <Stat label="Overall accuracy" value={`${data.overall_accuracy.toFixed(0)}%`} icon={<Target size={17} />} />
          <Stat label="Average time" value={`${data.avg_response_time.toFixed(0)}s`} hint="Per question" />
          <Stat label="Best score" value={`${data.best_percentage.toFixed(0)}%`} tone="success" />
          <Stat
            label="Improvement"
            value={`${data.improvement >= 0 ? "+" : ""}${data.improvement.toFixed(1)}`}
            hint="Recent vs previous window"
            tone={data.improvement >= 0 ? "success" : "warning"}
            icon={<TrendingUp size={17} />}
          />
        </div>
      </div>

      <div className="split-grid">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Progress</div><h3>Score trajectory</h3></div>
          </div>
          <TrendChart points={(data.trend as Array<{ percentage: number }>).map((row) => ({ percentage: row.percentage }))} />
          <div className="trend-caption"><span>Earlier</span><span>Latest</span></div>
        </Card>
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Difficulty</div><h3>Response by level</h3></div>
          </div>
          <div className="difficulty-grid">
            {difficulties.length ? difficulties.map((row) => (
              <div className="difficulty-row" key={row.difficulty}>
                <div className="difficulty-label">
                  <span>{titleCase(row.difficulty)}</span>
                  <Badge tone={scoreTone(row.accuracy)}>{row.accuracy.toFixed(0)}%</Badge>
                </div>
                <Progress value={row.accuracy} tone={row.accuracy >= 75 ? "success" : row.accuracy >= 50 ? "warning" : "danger"} />
                <span className="muted">{row.correct} of {row.attempted} correct · {row.avg_time.toFixed(0)}s avg</span>
              </div>
            )) : <EmptyState title="No difficulty data yet" description="Submit a practice attempt to populate this view." />}
          </div>
        </Card>
      </div>

      <div className="split-grid split-grid-wide">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Mastery map</div><h3>All topics</h3></div>
            <Badge tone="neutral">{data.topic_performance.length} topics</Badge>
          </div>
          <TopicBars
            items={data.topic_performance.map((topic) => ({
              label: topic.topic_name,
              percentage: topic.accuracy,
              detail: `${topic.correct}/${topic.attempted}`,
            }))}
          />
          <div className="topic-table">
            {data.topic_performance.map((topic) => (
              <div className="topic-table-row" key={topic.topic_id}>
                <div>
                  <strong>{topic.topic_name}</strong>
                  <span>{topic.subject_name}</span>
                </div>
                <span className="muted">{topic.attempted} attempted</span>
                <Badge tone={topic.strength === "STRONG" ? "success" : topic.strength === "WEAK" ? "danger" : "warning"}>
                  {titleCase(topic.strength)}
                </Badge>
                <strong className="topic-accuracy">{topic.accuracy.toFixed(0)}%</strong>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Next best action</div><h3>Recommendations</h3></div>
            <Lightbulb size={18} className="muted" />
          </div>
          <div className="recommendation-list">
            {(data.recommendations as Array<{
              title: string;
              action: string;
              priority: string;
              accuracy: number;
              practice_tests: Array<{ id: string; title: string; question_count: number }>;
            }>).map((rec, index) => (
              <div className="recommendation" key={index}>
                <div className="recommendation-icon"><Lightbulb size={16} /></div>
                <div>
                  <div className="recommendation-title">
                    {rec.title}
                    <Badge tone={rec.priority === "HIGH" ? "danger" : rec.priority === "MEDIUM" ? "warning" : "neutral"}>
                      {rec.priority}
                    </Badge>
                  </div>
                  <p>{rec.action}</p>
                  {rec.practice_tests?.length > 0 && (
                    <div className="recommendation-links">
                      {rec.practice_tests.map((test) => (
                        <Link key={test.id} to={`/student/practice/${test.id}`} className="mini-link">
                          {test.title} <ArrowRight size={13} />
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
