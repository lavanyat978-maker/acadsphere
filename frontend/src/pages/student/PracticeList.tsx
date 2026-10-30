import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, BookOpenCheck, Clock3, Filter, Search, Sparkles, Target } from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { PracticeTest } from "../../lib/types";
import { Badge, Card, EmptyState, ErrorState, PageHeader, PageLoading } from "../../components/ui";
import { difficultyTone, scoreTone } from "../../lib/format";

export function PracticeList() {
  const tests = useApi<PracticeTest[]>("/practice/tests");
  const [query, setQuery] = useState("");
  const [subject, setSubject] = useState("ALL");
  const [status, setStatus] = useState("ALL");

  const subjects = useMemo(
    () => Array.from(new Set((tests.data ?? []).map((test) => test.subject_name))).sort(),
    [tests.data],
  );

  const filtered = useMemo(() => {
    return (tests.data ?? []).filter((test) => {
      const matchesQuery =
        !query ||
        test.title.toLowerCase().includes(query.toLowerCase()) ||
        test.topics.some((topic) => topic.toLowerCase().includes(query.toLowerCase()));
      const matchesSubject = subject === "ALL" || test.subject_name === subject;
      const matchesStatus =
        status === "ALL" ||
        (status === "FRESH" && test.attempt_count === 0) ||
        (status === "ATTEMPTED" && test.attempt_count > 0) ||
        (status === "STRONG" && (test.best_score ?? 0) >= 75);
      return matchesQuery && matchesSubject && matchesStatus;
    });
  }, [tests.data, query, subject, status]);

  if (tests.loading && !tests.data) return <PageLoading label="Loading practice library" />;
  if (tests.error) return <ErrorState message={tests.error} onRetry={tests.reload} />;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Adaptive practice"
        title="Practice library"
        description="Short, topic-scoped tests that feed your analytics after every submission."
      />

      <Card className="filter-bar">
        <div className="search-wrap">
          <Search size={16} />
          <input
            className="search-input"
            placeholder="Search tests or topics…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="filter-group">
          <Filter size={15} />
          <select className="input select compact" value={subject} onChange={(event) => setSubject(event.target.value)}>
            <option value="ALL">All subjects</option>
            {subjects.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <select className="input select compact" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="ALL">Any progress</option>
            <option value="FRESH">Not attempted</option>
            <option value="ATTEMPTED">Attempted</option>
            <option value="STRONG">75% and above</option>
          </select>
        </div>
      </Card>

      {filtered.length ? (
        <div className="practice-grid">
          {filtered.map((test) => (
            <Card key={test.id} className="practice-card" as="article">
              <div className="practice-card-top">
                <Badge tone="neutral">{test.subject_name}</Badge>
                <Badge tone={difficultyTone(test.difficulty)}>{test.difficulty.toLowerCase()}</Badge>
              </div>
              <h3>{test.title}</h3>
              <p className="practice-description">{test.description || "Topic-focused practice set."}</p>
              <div className="topic-tags">
                {test.topics.slice(0, 4).map((topic) => (
                  <span key={topic} className="tag">{topic}</span>
                ))}
              </div>
              <div className="practice-meta">
                <span><BookOpenCheck size={14} /> {test.question_count} questions</span>
                <span><Clock3 size={14} /> {test.duration_minutes} min</span>
                <span><Target size={14} /> {test.total_marks} marks</span>
              </div>
              <div className="practice-card-foot">
                {test.best_score !== null ? (
                  <div className="best-score">
                    <span>Best</span>
                    <Badge tone={scoreTone(test.best_score)}>{test.best_score.toFixed(0)}%</Badge>
                  </div>
                ) : (
                  <span className="muted"><Sparkles size={13} /> Not attempted yet</span>
                )}
                <Link className="card-action" to={`/student/practice/${test.id}`}>
                  Open <ArrowRight size={15} />
                </Link>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <EmptyState
            icon={<BookOpenCheck size={22} />}
            title="No practice tests match"
            description="Try clearing the filters. You only see tests for subjects you are enrolled in; if a subject is missing, ask the Head of Department to enrol you."
          />
        </Card>
      )}
    </div>
  );
}
