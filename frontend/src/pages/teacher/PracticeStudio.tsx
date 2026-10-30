import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, BookOpenCheck, Clock3, Plus, Search, Target, Users } from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { PracticeTest } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, PageLoading } from "../../components/ui";
import { difficultyTone, formatDate, statusTone, titleCase } from "../../lib/format";

export function PracticeStudio() {
  const tests = useApi<PracticeTest[]>("/practice/staff/tests");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");

  const filtered = useMemo(
    () =>
      (tests.data ?? []).filter((test) => {
        const matchesQuery = !query || test.title.toLowerCase().includes(query.toLowerCase());
        const matchesStatus = status === "ALL" || test.status === status;
        return matchesQuery && matchesStatus;
      }),
    [tests.data, query, status],
  );

  if (tests.loading && !tests.data) return <PageLoading label="Loading practice studio" />;
  if (tests.error) return <ErrorState message={tests.error} onRetry={tests.reload} />;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Practice studio"
        title="Practice sets"
        description="Build topic-scoped practice, generate questions from syllabus material, then publish."
        actions={
          <Link to="/teacher/practice/new">
            <Button icon={<Plus size={16} />}>New practice set</Button>
          </Link>
        }
      />

      <Card className="filter-bar">
        <div className="search-wrap">
          <Search size={16} />
          <input
            className="search-input"
            placeholder="Search your practice sets…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <select className="input select compact" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="ALL">All statuses</option>
          <option value="DRAFT">Draft</option>
          <option value="PUBLISHED">Published</option>
          <option value="ARCHIVED">Archived</option>
        </select>
      </Card>

      {filtered.length ? (
        <div className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Practice set</th>
                <th>Subject</th>
                <th>Structure</th>
                <th>Attempts</th>
                <th>Status</th>
                <th aria-label="Open" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((test) => (
                <tr key={test.id}>
                  <td>
                    <div className="table-primary">
                      <strong>{test.title}</strong>
                      <span>
                        {test.topics.slice(0, 3).join(" · ") || "No topics"}
                        {!test.can_edit && ` · view only (${test.created_by_name})`}
                      </span>
                    </div>
                  </td>
                  <td className="muted">{test.subject_name}</td>
                  <td>
                    <div className="table-inline">
                      <span><BookOpenCheck size={13} /> {test.question_count} Q</span>
                      <span><Target size={13} /> {test.total_marks} marks</span>
                      <span><Clock3 size={13} /> {test.duration_minutes} min</span>
                    </div>
                  </td>
                  <td>
                    <span className="inline-stat"><Users size={13} /> {test.attempt_count}</span>
                  </td>
                  <td>
                    <div className="status-stack">
                      <Badge tone={statusTone(test.status)}>{titleCase(test.status)}</Badge>
                      <Badge tone={difficultyTone(test.difficulty)}>{test.difficulty.toLowerCase()}</Badge>
                    </div>
                  </td>
                  <td className="cell-action">
                    <Link to={`/teacher/practice/${test.id}`} className="icon-link" aria-label="Manage practice set">
                      <ArrowRight size={16} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Card>
          <EmptyState
            icon={<BookOpenCheck size={22} />}
            title="No practice sets yet"
            description="Create a draft, generate questions from your syllabus, then publish to students."
            action={<Link to="/teacher/practice/new"><Button>Create your first set</Button></Link>}
          />
        </Card>
      )}

      <p className="muted small">
        As of {formatDate(new Date().toISOString())} · students only see published sets in subjects they are enrolled in.
      </p>
    </div>
  );
}
