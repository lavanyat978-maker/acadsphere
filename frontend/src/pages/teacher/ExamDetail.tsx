import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  FileKey2,
  FileLock2,
  Fingerprint,
  ShieldCheck,
  Sparkles,
  Timer,
  TriangleAlert,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { Exam, GenerationStatus, SecurePaper } from "../../lib/types";
import { Badge, Button, Card, ErrorState, KeyValue, PageHeader, PageLoading, Progress } from "../../components/ui";
import { bytes, formatDateTime, statusTone, titleCase } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

const LIFECYCLE = ["SUBMITTED", "GENERATING", "VALIDATING", "ASSEMBLED", "VALIDATED", "LOCKED", "ENCRYPTED", "STORED"];

export function ExamDetail() {
  const { examId = "" } = useParams();
  const exam = useApi<Exam>(`/exams/${examId}`);
  const generation = useApi<GenerationStatus>(
    exam.data?.generation_request_id ? `/exams/${examId}/generation` : null,
    { pollMs: 3000 },
  );
  const paper = useApi<SecurePaper>(
    exam.data?.paper_state ? `/exams/${examId}/paper` : null,
  );
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const status = generation.data?.status;
    if (status === "COMPLETED" || status === "FAILED") {
      exam.reload();
    }
  }, [generation.data?.status, exam.reload]);

  if (exam.loading && !exam.data) return <PageLoading label="Opening examination" />;
  if (exam.error) return <ErrorState message={exam.error} onRetry={exam.reload} />;
  if (!exam.data) return null;
  const data = exam.data;
  const config = data.configuration;
  const isDraft = data.status === "DRAFT";
  const isBusy = generation.data?.status === "RUNNING" || generation.data?.status === "QUEUED";

  const submitConfig = async () => {
    setBusy(true);
    try {
      await api(`/exams/${data.id}/submit`, { method: "POST" });
      toast("Blueprint frozen. Generation is now available.", "success");
      exam.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    try {
      await api(`/exams/${data.id}/generate`, { method: "POST" });
      toast("Generation queued. This view will update automatically.", "success");
      exam.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const paperState = paper.data?.state ?? data.paper_state;
  const activeStage = paperState && LIFECYCLE.includes(paperState) ? paperState : generation.data?.stage ?? data.status;
  const currentIndex = LIFECYCLE.indexOf(activeStage);

  return (
    <div className="stack">
      <Link to="/teacher/exams" className="back-link"><ArrowLeft size={15} /> Examinations</Link>
      <PageHeader
        eyebrow={`${data.subject_name} · ${data.exam_type}`}
        title={data.title}
        description={`${data.duration_minutes} minutes · ${data.venue} · ${formatDateTime(data.exam_date)}`}
        actions={
          <div className="page-actions-inline">
            <Badge tone={statusTone(data.status)}>{titleCase(data.status)}</Badge>
            {paperState && <Badge tone={statusTone(paperState)} dot>{titleCase(paperState)}</Badge>}
            {isDraft && (
              <Button onClick={submitConfig} loading={busy} icon={<FileLock2 size={15} />}>
                Freeze blueprint
              </Button>
            )}
            {!isDraft && !paperState && (
              <Button onClick={generate} loading={busy || isBusy} icon={<Sparkles size={15} />}>
                {isBusy ? "Generating…" : "Generate sealed paper"}
              </Button>
            )}
          </div>
        }
      />

      {generation.data && (
        <Card className={`generation-card generation-${generation.data.status.toLowerCase()}`}>
          <div className="generation-head">
            <div className="generation-icon">
              {generation.data.status === "COMPLETED" ? <CheckCircle2 size={22} /> : generation.data.status === "FAILED" ? <TriangleAlert size={22} /> : <Sparkles size={22} />}
            </div>
            <div>
              <div className="eyebrow">Generation pipeline</div>
              <h3>{generation.data.secure_summary}</h3>
              <p>
                {generation.data.mode} mode · {generation.data.provider} · {generation.data.candidates_generated} candidates generated,{" "}
                {generation.data.candidates_rejected} rejected across {generation.data.attempts} attempts.
              </p>
            </div>
            <Badge tone={statusTone(generation.data.status)}>{titleCase(generation.data.status)}</Badge>
          </div>
          <Progress value={generation.data.progress} label={`Stage: ${titleCase(generation.data.stage)}`} />
          {generation.data.error && <div className="alert alert-danger">{generation.data.error}</div>}
          <div className="validation-chips">
            {Object.entries(generation.data.validation).map(([level, counts]) => (
              <span className="validation-chip" key={level}>
                <strong>{level === "paper" ? "Level 5 · paper" : level.replace("_", " ")}</strong>
                <span>{counts.passed} passed · {counts.failed} failed</span>
              </span>
            ))}
          </div>
        </Card>
      )}

      {!generation.data && !isDraft && (
        <Card className="stage-note">
          <ShieldCheck size={19} />
          <div>
            <strong>Blueprint frozen and ready</strong>
            <p>Request generation to ground questions in the uploaded syllabus, validate candidates, assemble the paper and encrypt it.</p>
          </div>
          <Button onClick={generate} loading={busy} icon={<Sparkles size={15} />}>Generate</Button>
        </Card>
      )}

      <div className="split-grid split-grid-wide">
        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Lifecycle</div><h3>Paper state machine</h3></div>
            <Badge tone="neutral">Server enforced</Badge>
          </div>
          <div className="lifecycle">
            {(paper.data?.transitions.length
              ? paper.data.transitions.map((transition) => ({ state: transition.to, at: transition.at, reason: transition.reason, role: transition.actor_role }))
              : LIFECYCLE.map((state) => ({ state, at: null, reason: "", role: "" }))
            ).map((stage, index) => {
              const done = paper.data?.transitions.length ? true : index <= currentIndex;
              return (
                <div className={`lifecycle-step ${done ? "done" : ""}`} key={`${stage.state}-${index}`}>
                  <span className="lifecycle-dot" />
                  <div>
                    <strong>{titleCase(stage.state)}</strong>
                    <span>{stage.at ? formatDateTime(stage.at) : stage.state === activeStage ? "In progress" : "Pending"}</span>
                    {stage.reason && <em>{stage.reason} · {stage.role}</em>}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <div className="card-head">
            <div><div className="eyebrow">Frozen blueprint</div><h3>Configuration</h3></div>
            <FileLock2 size={17} className="muted" />
          </div>
          {config ? (
            <>
              <KeyValue
                items={[
                  { label: "Total marks", value: config.total_marks },
                  { label: "Question groups", value: config.pattern.length },
                  { label: "Retry limit", value: config.retry_limit },
                  { label: "Frozen", value: config.frozen ? <Badge tone="success">Yes</Badge> : <Badge tone="warning">Draft</Badge> },
                  { label: "Frozen at", value: config.frozen_at ? formatDateTime(config.frozen_at) : "—" },
                  { label: "Blueprint hash", value: <code className="hash">{config.frozen_hash ? `${config.frozen_hash.slice(0, 16)}…` : "—"}</code> },
                ]}
              />
              <div className="pattern-chips">
                {config.pattern.map((row) => (
                  <span className="tag" key={row.label}>{row.label}: {row.count} × {row.marks} marks</span>
                ))}
              </div>
              <div className="distribution-row">
                {Object.entries(config.difficulty_distribution).map(([key, value]) => (
                  <Badge tone="neutral" key={key}>{key.toLowerCase()} {value}%</Badge>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Configuration unavailable.</p>
          )}
          <div className="meta-list">
            <div><CalendarDays size={15} /><span>Exam {formatDateTime(data.exam_date)}</span></div>
            <div><Timer size={15} /><span>{data.duration_minutes} minutes</span></div>
            <div><ShieldCheck size={15} /><span>Owner {data.created_by_name}</span></div>
          </div>
        </Card>
      </div>

      {paper.data ? (
        <>
          <Card className="secure-paper-card">
            <div className="secure-paper-head">
              <div className="secure-icon"><FileKey2 size={20} /></div>
              <div>
                <div className="eyebrow">Encrypted artifact</div>
                <h3>{paper.data.algorithm} · key {paper.data.key_id}</h3>
                <p>Ciphertext lives in secure object storage. The wrapped data key is stored separately from the payload nonce.</p>
              </div>
              <Badge tone={statusTone(paper.data.state)}>{titleCase(paper.data.state)}</Badge>
            </div>
            <div className="secure-stats">
              <div><span>Ciphertext</span><strong>{bytes(paper.data.ciphertext_bytes)}</strong></div>
              <div><span>Questions</span><strong>{paper.data.question_count}</strong></div>
              <div><span>Total marks</span><strong>{paper.data.total_marks}</strong></div>
              <div><span>Hash verified</span><strong>{paper.data.plaintext_hash_matches ? "Yes" : "No"}</strong></div>
            </div>
            <div className="hash-block">
              <Fingerprint size={15} />
              <div><span>SHA-256 ciphertext</span><code>{paper.data.ciphertext_sha256}</code></div>
            </div>
            <div className="alert alert-info">
              <ShieldCheck size={16} />
              Question plaintext is not available to faculty. The HOD release ceremony decrypts in memory only during the window.
            </div>
          </Card>

          <Card>
            <div className="card-head"><div><div className="eyebrow">Post-generation hold</div><h3>What happens next</h3></div></div>
            <div className="next-steps">
              <div><span>1</span><div><strong>HOD release window</strong><p>Opens 24 hours before the exam and closes two hours after the start time.</p></div></div>
              <div><span>2</span><div><strong>MFA-authorized decryption</strong><p>The HOD receives a short-lived release token and verifies ciphertext integrity.</p></div></div>
              <div><span>3</span><div><strong>Controlled printing</strong><p>Maximum three prints. Every decrypt and print is written to the tamper-evident audit trail.</p></div></div>
            </div>
          </Card>
        </>
      ) : (
        <Card>
          <div className="card-head"><div><div className="eyebrow">Sealed output</div><h3>No paper artifact yet</h3></div></div>
          <p className="muted">
            Once generation completes, this panel shows encryption metadata and a state-machine timeline. Question text will remain sealed.
          </p>
        </Card>
      )}
    </div>
  );
}
