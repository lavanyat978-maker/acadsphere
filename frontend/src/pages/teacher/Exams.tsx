import { Link } from "react-router-dom";
import { ArrowRight, CalendarDays, FileLock2, Plus, ShieldCheck, MapPin } from "lucide-react";
import { useApi } from "../../lib/useApi";
import type { Exam } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, PageLoading } from "../../components/ui";
import { formatDateTime, statusTone, titleCase } from "../../lib/format";

export function Exams() {
  const exams = useApi<Exam[]>("/exams");

  if (exams.loading && !exams.data) return <PageLoading label="Loading examinations" />;
  if (exams.error) return <ErrorState message={exams.error} onRetry={exams.reload} />;
  const rows = exams.data ?? [];

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Internal examinations"
        title="Examinations"
        description="Frozen configurations, secure generation and paper lifecycle. Question text never reaches this workspace."
        actions={<Link to="/teacher/exams/new"><Button icon={<Plus size={16} />}>Configure examination</Button></Link>}
      />

      <div className="exam-grid">
        {rows.length ? rows.map((exam) => {
          const paperState = exam.paper_state;
          return (
            <Card className="exam-card" key={exam.id} as="article">
              <div className="exam-card-top">
                <Badge tone={statusTone(exam.status)}>{titleCase(exam.status)}</Badge>
                {paperState && <Badge tone={statusTone(paperState)} dot>{titleCase(paperState)}</Badge>}
              </div>
              <h3>{exam.title}</h3>
              <p className="exam-sub">{exam.subject_name} · {exam.exam_type}</p>
              <div className="exam-meta-grid">
                <div><CalendarDays size={15} /><span>{formatDateTime(exam.exam_date)}</span></div>
                <div><ShieldCheck size={15} /><span>{exam.total_marks} marks · {exam.configuration?.pattern.reduce((sum, item) => sum + item.count, 0) ?? 0} questions</span></div>
                <div><MapPin size={15} /><span>{exam.venue}</span></div>
                <div><FileLock2 size={15} /><span>{exam.duration_minutes} minutes</span></div>
              </div>
              <div className="exam-topics">
                {(exam.topics as Array<{ name: string }>).slice(0, 3).map((topic) => (
                  <span className="tag" key={topic.name}>{topic.name}</span>
                ))}
                {exam.topics.length > 3 && <span className="tag">+{exam.topics.length - 3}</span>}
              </div>
              <div className="exam-card-foot">
                <span className="muted">
                  {exam.configuration?.frozen ? "Configuration frozen" : "Configuration editable"}
                </span>
                <Link to={`/teacher/exams/${exam.id}`} className="card-action">
                  Open <ArrowRight size={15} />
                </Link>
              </div>
            </Card>
          );
        }) : (
          <Card className="span-all">
            <EmptyState
              icon={<FileLock2 size={22} />}
              title="No examinations yet"
              description="Configure a pattern, select topics and freeze the blueprint. Generation happens next."
              action={<Link to="/teacher/exams/new"><Button>Configure your first exam</Button></Link>}
            />
          </Card>
        )}
      </div>
    </div>
  );
}
