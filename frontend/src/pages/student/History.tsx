import { Link } from "react-router-dom";
import { ArrowRight, ClipboardList } from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { HistoryItem } from "../../lib/types";
import { Badge, Card, EmptyState, ErrorState, PageHeader, PageLoading } from "../../components/ui";
import { formatDateTime, scoreTone, titleCase } from "../../lib/format";

export function History() {
  const history = useApi<HistoryItem[]>("/practice/history");

  if (history.loading && !history.data) return <PageLoading label="Loading attempt history" />;
  if (history.error) return <ErrorState message={history.error} onRetry={history.reload} />;
  const rows = history.data ?? [];

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Attempt archive"
        title="History"
        description="Every practice attempt, including in-progress sessions and auto-submitted deadlines."
      />

      <Card>
        {rows.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Test</th>
                  <th>Subject</th>
                  <th>Started</th>
                  <th>Score</th>
                  <th>Accuracy</th>
                  <th>Status</th>
                  <th aria-label="Open" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.attempt_id}>
                    <td><strong>{row.test_title}</strong></td>
                    <td className="muted">{row.subject_name}</td>
                    <td className="muted">{formatDateTime(row.started_at)}</td>
                    <td>
                      {row.status === "IN_PROGRESS" ? (
                        <span className="muted">—</span>
                      ) : (
                        <Badge tone={scoreTone(row.percentage)}>{row.percentage.toFixed(1)}%</Badge>
                      )}
                    </td>
                    <td>{row.status === "IN_PROGRESS" ? "—" : `${row.accuracy.toFixed(1)}%`}</td>
                    <td><Badge tone={row.status === "IN_PROGRESS" ? "warning" : row.status === "SUBMITTED" ? "success" : "neutral"}>{titleCase(row.status)}</Badge></td>
                    <td className="cell-action">
                      <Link
                        to={row.status === "IN_PROGRESS" ? `/student/attempt/${row.attempt_id}` : `/student/results/${row.attempt_id}`}
                        className="icon-link"
                        aria-label={row.status === "IN_PROGRESS" ? "Resume attempt" : "View result"}
                      >
                        <ArrowRight size={16} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<ClipboardList size={22} />}
            title="No attempts recorded"
            description="Start a practice set to build your history."
            action={<Link to="/student/practice" className="text-link">Browse practice <ArrowRight size={14} /></Link>}
          />
        )}
      </Card>
    </div>
  );
}
