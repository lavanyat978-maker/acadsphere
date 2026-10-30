import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  FileKey2,
  KeyRound,
  ScanLine,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useApi } from "../../lib/useApi";
import type { HodDashboard as HodDashboardData } from "../../lib/types";
import { Badge, Button, Card, ErrorState, PageHeader, PageLoading, Stat } from "../../components/ui";
import { formatDateTime, relativeTime, scoreTone, statusTone, titleCase, firstName as firstNameOf } from "../../lib/format";

export function HodDashboard() {
  const { user } = useAuth();
  const dashboard = useApi<HodDashboardData>("/analytics/dashboard");

  if (dashboard.loading && !dashboard.data) return <PageLoading label="Assembling command centre" />;
  if (dashboard.error) return <ErrorState message={dashboard.error} onRetry={dashboard.reload} />;
  if (!dashboard.data) return null;
  const data = dashboard.data;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Department control"
        title={`Command centre${user ? ` · ${firstNameOf(user.full_name)}` : ""}`}
        description="Sealed paper inventory, release windows and the security posture of the assessment cycle."
        actions={
          <Link to="/hod/releases">
            <Button icon={<ScanLine size={16} />}>Open release control</Button>
          </Link>
        }
      />

      <div className="stat-grid">
        <Stat label="Secured papers" value={data.secured_papers} hint="Encrypted, awaiting release" icon={<FileKey2 size={18} />} tone="success" />
        <Stat label="Pending release" value={data.pending_release} hint="Exam within 24 hours" icon={<CalendarClock size={18} />} tone="warning" />
        <Stat label="Released papers" value={data.released_papers} hint="Printed and handed over" icon={<ShieldCheck size={18} />} />
        <Stat label="Security events" value={data.security_events.length} hint="Recent control-plane signals" icon={<ShieldAlert size={18} />} tone="danger" />
      </div>

      <div className="split-grid split-grid-wide">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Release calendar</div><h3>Next examinations</h3></div>
            <Badge tone="neutral">{data.upcoming_exams} upcoming</Badge>
          </div>
          {data.next_exams.length ? (
            <div className="exam-list">
              {data.next_exams.map((exam) => (
                <Link to="/hod/releases" className="exam-row" key={exam.id}>
                  <div className="exam-date">
                    <strong>{new Date(exam.exam_date).getDate()}</strong>
                    <span>{new Date(exam.exam_date).toLocaleDateString(undefined, { month: "short" })}</span>
                  </div>
                  <div className="exam-main">
                    <strong>{exam.title}</strong>
                    <span>{exam.exam_type} · {exam.total_marks} marks · starts {relativeTime(exam.exam_date)}</span>
                  </div>
                  <Badge tone={statusTone(exam.status)}>{titleCase(exam.status)}</Badge>
                  <ArrowRight size={16} className="muted" />
                </Link>
              ))}
            </div>
          ) : (
            <p className="muted">No examinations scheduled.</p>
          )}
        </Card>

        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Control plane</div><h3>Security events</h3></div>
            <Link to="/hod/security" className="text-link">All events <ArrowRight size={14} /></Link>
          </div>
          {data.security_events.length ? (
            <div className="security-list">
              {data.security_events.slice(0, 5).map((event) => (
                <div className="security-row" key={event.id}>
                  <span className={`severity severity-${event.severity.toLowerCase()}`}>{event.severity === "WARNING" || event.severity === "CRITICAL" ? <AlertTriangle size={14} /> : <ShieldCheck size={14} />}</span>
                  <div>
                    <strong>{titleCase(event.event_type)}</strong>
                    <span>{event.summary}</span>
                  </div>
                  <span className="muted">{relativeTime(event.created_at)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No security events recorded.</p>
          )}
        </Card>
      </div>

      <div className="split-grid">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Audit</div><h3>Recent release actions</h3></div>
            <Link to="/hod/audit" className="text-link">Audit trail <ArrowRight size={14} /></Link>
          </div>
          {data.recent_releases.length ? (
            <div className="activity-list">
              {data.recent_releases.map((release) => (
                <div className="activity-row" key={release.id}>
                  <div className="activity-main">
                    <strong>Paper {release.paper_id.slice(0, 8)}</strong>
                    <span>Authorized {formatDateTime(release.authorized_at)}</span>
                  </div>
                  <div className="activity-score">
                    <Badge tone={statusTone(release.status)}>{titleCase(release.status)}</Badge>
                    <span>{release.print_count} print{release.print_count === 1 ? "" : "s"}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No release actions recorded.</p>
          )}
        </Card>

        <Card className="hod-ceremony">
          <div className="card-head">
            <div><div className="eyebrow">Release ceremony</div><h3>Four controlled steps</h3></div>
            <KeyRound size={18} className="muted" />
          </div>
          <ol className="ceremony-steps">
            <li><span>1</span><div><strong>Authorize</strong><p>MFA context + reason. Issues a 10-minute release token.</p></div></li>
            <li><span>2</span><div><strong>Decrypt &amp; verify</strong><p>Ciphertext hash and plaintext hash must both match.</p></div></li>
            <li><span>3</span><div><strong>Print</strong><p>PDF is generated in memory. Three-print cap is enforced server-side.</p></div></li>
            <li><span>4</span><div><strong>Mark released</strong><p>Closes the lifecycle and records the handover.</p></div></li>
          </ol>
        </Card>
      </div>

      {data.department && (
        <>
          <div className="card-head compact">
            <div>
              <div className="eyebrow">Department analytics · practice module</div>
              <h3>
                {data.department.students} active students · {data.department.practice_attempts} attempts ·{" "}
                {data.department.average_percentage.toFixed(1)}% average
              </h3>
            </div>
            <Link to="/hod/subjects" className="text-link">Manage subjects <ArrowRight size={14} /></Link>
          </div>
          <div className="split-grid">
            <div className="table-card">
              <div className="table-wrap">
                <table className="data-table data-table-compact">
                  <thead>
                    <tr><th>Subject</th><th>Staff</th><th>Students</th><th>Tests</th><th>Attempts</th><th>Average</th></tr>
                  </thead>
                  <tbody>
                    {data.department.subjects.map((row) => (
                      <tr key={row.subject_id}>
                        <td className="table-primary"><strong>{row.code}</strong><span>{row.name}</span></td>
                        <td>{row.teachers}</td>
                        <td>{row.active_students}/{row.students}</td>
                        <td>{row.published_tests}</td>
                        <td>{row.attempts}</td>
                        <td>{row.attempts ? <Badge tone={scoreTone(row.average_percentage)}>{row.average_percentage.toFixed(1)}%</Badge> : <span className="muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="table-card">
              <div className="table-wrap">
                <table className="data-table data-table-compact">
                  <thead>
                    <tr><th>Teacher</th><th>Subjects</th><th>Tests</th><th>Attempts</th><th>Average</th><th>Exams</th></tr>
                  </thead>
                  <tbody>
                    {data.department.teachers.map((row) => (
                      <tr key={row.teacher_id}>
                        <td className="table-primary"><strong>{row.name}</strong></td>
                        <td>{row.subjects}</td>
                        <td>{row.published_tests}/{row.practice_tests}</td>
                        <td>{row.attempts}</td>
                        <td>{row.attempts ? <Badge tone={scoreTone(row.average_percentage)}>{row.average_percentage.toFixed(1)}%</Badge> : <span className="muted">—</span>}</td>
                        <td>{row.exams}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
