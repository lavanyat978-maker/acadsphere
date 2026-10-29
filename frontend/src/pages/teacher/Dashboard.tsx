import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpenCheck,
  FileLock2,
  FileSearch,
  Layers,
  Plus,
  ShieldCheck,
  Users,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useApi } from "../../lib/useApi";
import type { TeacherDashboard as TeacherDashboardData } from "../../lib/types";
import { formatDate, statusTone, titleCase } from "../../lib/format";
import { Badge, Button, Card, ErrorState, PageHeader, PageLoading, Stat } from "../../components/ui";

export function TeacherDashboard() {
  const { user } = useAuth();
  const dashboard = useApi<TeacherDashboardData>("/analytics/dashboard");

  if (dashboard.loading && !dashboard.data) return <PageLoading label="Preparing faculty overview" />;
  if (dashboard.error) return <ErrorState message={dashboard.error} onRetry={dashboard.reload} />;
  if (!dashboard.data) return null;
  const data = dashboard.data;
  const firstName = user?.full_name.split(" ")[1] ?? user?.full_name.split(" ")[0] ?? "Professor";

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Faculty workspace"
        title={`Welcome, ${firstName}.`}
        description="Author practice sets, generate sealed internal papers, and monitor cohort performance."
        actions={
          <div className="page-actions-inline">
            <Link to="/teacher/practice">
              <Button variant="secondary" icon={<Plus size={16} />}>New practice set</Button>
            </Link>
            <Link to="/teacher/exams">
              <Button icon={<FileLock2 size={16} />}>Configure exam</Button>
            </Link>
          </div>
        }
      />

      <div className="stat-grid">
        <Stat label="Subjects assigned" value={data.subjects} hint="Across the department" icon={<Layers size={18} />} tone="accent" />
        <Stat label="Practice sets" value={data.practice_tests} hint={`${data.published_tests} published`} icon={<BookOpenCheck size={18} />} />
        <Stat label="Student attempts" value={data.total_attempts} hint={`${data.active_students} active students`} icon={<Users size={18} />} />
        <Stat label="Secured papers" value={data.secured_papers} hint="Encrypted and sealed" icon={<ShieldCheck size={18} />} tone="success" />
      </div>

      <div className="split-grid split-grid-wide">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Assessment pipeline</div><h3>Recent examinations</h3></div>
            <Link to="/teacher/exams" className="text-link">All exams <ArrowRight size={14} /></Link>
          </div>
          {data.recent_exams.length ? (
            <div className="exam-list">
              {data.recent_exams.map((exam) => (
                <Link to={`/teacher/exams/${exam.id}`} className="exam-row" key={exam.id}>
                  <div className="exam-date">
                    <strong>{new Date(exam.exam_date).getDate()}</strong>
                    <span>{new Date(exam.exam_date).toLocaleDateString(undefined, { month: "short" })}</span>
                  </div>
                  <div className="exam-main">
                    <strong>{exam.title}</strong>
                    <span>{exam.exam_type} · {exam.total_marks} marks · {formatDate(exam.exam_date)}</span>
                  </div>
                  <Badge tone={statusTone(exam.status)}>{titleCase(exam.status)}</Badge>
                  <ArrowRight size={16} className="muted" />
                </Link>
              ))}
            </div>
          ) : (
            <div className="empty-inline">
              <FileLock2 size={20} />
              <div>
                <strong>No examination configured</strong>
                <p>Configure an exam to generate a syllabus-grounded, validated paper.</p>
              </div>
              <Link to="/teacher/exams/new"><Button size="sm">Create exam</Button></Link>
            </div>
          )}
        </Card>

        <Card className="workflow-card">
          <div className="card-head">
            <div><div className="eyebrow">Controlled workflow</div><h3>How a paper gets sealed</h3></div>
            <ShieldCheck size={18} className="muted" />
          </div>
          <ol className="workflow-list">
            <li><span>01</span><div><strong>Configure &amp; freeze</strong><p>Pattern, topics, difficulty and Bloom weights are hashed and frozen.</p></div></li>
            <li><span>02</span><div><strong>Generate &amp; validate</strong><p>RAG grounding, four candidate checks, deterministic assembly, final paper check.</p></div></li>
            <li><span>03</span><div><strong>Lock &amp; encrypt</strong><p>AES-256-GCM envelope encryption. Selected pool text is sealed.</p></div></li>
            <li><span>04</span><div><strong>HOD release</strong><p>MFA-gated decryption inside the release window, three controlled prints.</p></div></li>
          </ol>
          <div className="workflow-note">
            <FileSearch size={15} />
            <span>Faculty can inspect lifecycle metadata but never question text.</span>
          </div>
        </Card>
      </div>

      <Card className="teacher-cta">
        <div>
          <div className="eyebrow">Syllabus grounding</div>
          <h3>Richer source material produces stronger questions.</h3>
          <p>Upload unit-wise notes or syllabi as PDF, DOCX or TXT. Praxis chunks, embeds and retrieves them during generation.</p>
        </div>
        <Link to="/teacher/syllabus">
          <Button variant="secondary" icon={<ArrowRight size={16} />}>Open syllabus library</Button>
        </Link>
      </Card>
    </div>
  );
}
