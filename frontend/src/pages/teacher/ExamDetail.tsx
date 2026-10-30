import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Ban,
  CalendarDays,
  CheckCircle2,
  Eye,
  FileKey2,
  FileLock2,
  FileUp,
  Fingerprint,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Timer,
  TriangleAlert,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { Exam, GenerationStatus, SecurePaper } from "../../lib/types";
import { Badge, Button, Card, ErrorState, Field, KeyValue, Modal, PageHeader, PageLoading, Progress, Textarea } from "../../components/ui";
import { bytes, formatDateTime, statusTone, titleCase } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

const GENERATED_LIFECYCLE = ["SUBMITTED", "GENERATING", "VALIDATING", "ASSEMBLED", "VALIDATED", "LOCKED", "ENCRYPTED", "STORED"];
const UPLOADED_LIFECYCLE = ["SUBMITTED", "LOCKED", "ENCRYPTED", "STORED"];
const CLOSED = new Set(["RELEASED", "CANCELLED"]);

export function ExamDetail() {
  const { examId = "" } = useParams();
  const exam = useApi<Exam>(`/exams/${examId}`);
  const running = exam.data?.generation_status === "RUNNING" || exam.data?.generation_status === "QUEUED";
  const generation = useApi<GenerationStatus>(
    exam.data?.generation_request_id ? `/exams/${examId}/generation` : null,
    { pollMs: running ? 3000 : undefined },
  );
  const paper = useApi<SecurePaper>(exam.data?.paper_state ? `/exams/${examId}/paper` : null);
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const generationStatus = generation.data?.status;
  useEffect(() => {
    if (generationStatus === "COMPLETED" || generationStatus === "FAILED") {
      exam.reload();
      paper.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generationStatus]);

  if (exam.loading && !exam.data) return <PageLoading label="Opening examination" />;
  if (exam.error) return <ErrorState message={exam.error} onRetry={exam.reload} />;
  if (!exam.data) return null;
  const data = exam.data;
  const config = data.configuration;
  const uploaded = data.source === "UPLOADED";
  const editable = data.can_edit && !CLOSED.has(data.status);
  const isDraft = data.status === "DRAFT";
  const isBusy = generation.data?.status === "RUNNING" || generation.data?.status === "QUEUED";
  const canGenerate = !uploaded && editable && (data.status === "SUBMITTED" || data.status === "FAILED") && !isBusy;
  const replaceDeadline = data.replaceable_until ? new Date(data.replaceable_until) : null;
  const canUpload = uploaded && editable && replaceDeadline !== null && replaceDeadline.getTime() > Date.now();
  const inRelease = ["RELEASE_PENDING", "DECRYPTED", "PRINTED", "RELEASED"].includes(data.paper_state ?? "");

  const reloadAll = () => {
    exam.reload();
    paper.reload();
    generation.reload();
  };

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
      toast(data.status === "FAILED" ? "Retry queued with a fresh paper version." : "Generation queued. This view updates automatically.", "success");
      exam.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const uploadPaper = async (file: File | undefined) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast("Upload the question paper as a PDF.", "error");
      return;
    }
    if (data.paper_id && !window.confirm("Replace the current paper? The previous version is destroyed and the change is audited.")) {
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      await api(`/exams/${data.id}/paper`, { method: "POST", form });
      toast("Paper encrypted and stored. Only the HOD can open it during the release window.", "success");
      reloadAll();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const cancelExam = async () => {
    setBusy(true);
    try {
      await api(`/exams/${data.id}/cancel`, { method: "POST", body: { reason: cancelReason } });
      toast("Examination cancelled and its paper destroyed.", "success");
      setCancelOpen(false);
      reloadAll();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const lifecycle = uploaded ? UPLOADED_LIFECYCLE : GENERATED_LIFECYCLE;
  const paperState = paper.data?.state ?? data.paper_state;
  const activeStage = paperState && lifecycle.includes(paperState) ? paperState : generation.data?.stage ?? data.status;
  const currentIndex = lifecycle.indexOf(activeStage);

  return (
    <div className="stack">
      <Link to="/teacher/exams" className="back-link"><ArrowLeft size={15} /> Examinations</Link>
      <PageHeader
        eyebrow={`${data.subject_name} · ${data.exam_type} · ${uploaded ? "Uploaded paper" : "AI-generated paper"}`}
        title={data.title}
        description={`${data.duration_minutes} minutes · ${data.venue} · ${formatDateTime(data.exam_date)}`}
        actions={
          <div className="page-actions-inline">
            <Badge tone={statusTone(data.status)}>{titleCase(data.status)}</Badge>
            {paperState && <Badge tone={statusTone(paperState)} dot>{titleCase(paperState)}{data.paper_version ? ` · v${data.paper_version}` : ""}</Badge>}
            {!uploaded && editable && isDraft && (
              <Button onClick={submitConfig} loading={busy} icon={<FileLock2 size={15} />}>
                Freeze blueprint
              </Button>
            )}
            {canGenerate && (
              <Button onClick={generate} loading={busy} icon={data.status === "FAILED" ? <RotateCcw size={15} /> : <Sparkles size={15} />}>
                {data.status === "FAILED" ? "Retry generation" : "Generate sealed paper"}
              </Button>
            )}
            {editable && !inRelease && (
              <Button variant="ghost" onClick={() => { setCancelReason(""); setCancelOpen(true); }} icon={<Ban size={15} />}>
                Cancel exam
              </Button>
            )}
          </div>
        }
      />

      {!data.can_edit && (
        <div className="notice">
          <Eye size={16} />
          <span>
            View only. {data.created_by_name} created this examination; only they or the HOD can change it.
          </span>
        </div>
      )}
      {data.status === "CANCELLED" && (
        <div className="notice notice-warning">
          <Ban size={16} />
          <span>This examination was cancelled. Its paper was discarded and the ciphertext destroyed.</span>
        </div>
      )}

      {uploaded && (
        <Card className="secure-paper-card">
          <div className="secure-paper-head">
            <div className="secure-icon"><FileUp size={20} /></div>
            <div>
              <div className="eyebrow">Prepared question paper</div>
              <h3>{data.paper_file_name ? data.paper_file_name : "No paper uploaded yet"}</h3>
              <p>
                {data.paper_file_name
                  ? `${data.paper_page_count} page(s), encrypted on upload. It cannot be downloaded again; keep your own source file safe.`
                  : "Upload the finished paper as a PDF (max 15 MB). It is encrypted immediately with AES-256-GCM."}
              </p>
            </div>
            {data.paper_file_name ? <Badge tone="success" dot>Sealed</Badge> : <Badge tone="warning" dot>Awaiting upload</Badge>}
          </div>
          {canUpload ? (
            <div className="upload-actions">
              <input
                ref={fileInput}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden-input"
                onChange={(event) => uploadPaper(event.target.files?.[0])}
              />
              <Button onClick={() => fileInput.current?.click()} loading={busy} icon={<FileUp size={15} />}>
                {data.paper_file_name ? "Replace paper" : "Upload paper"}
              </Button>
              {replaceDeadline && (
                <span className="muted">Replaceable until {formatDateTime(replaceDeadline.toISOString())} (24 hours before the exam).</span>
              )}
            </div>
          ) : (
            uploaded && data.can_edit && !CLOSED.has(data.status) && (
              <div className="alert alert-info">
                <ShieldCheck size={16} />
                The release window has opened, so the paper is frozen. Ask the HOD if a change is unavoidable.
              </div>
            )
          )}
        </Card>
      )}

      {!uploaded && generation.data && (
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

      {!uploaded && !generation.data && data.status === "SUBMITTED" && (
        <Card className="stage-note">
          <ShieldCheck size={19} />
          <div>
            <strong>Blueprint frozen and ready</strong>
            <p>Request generation to ground questions in the syllabus library, validate candidates, assemble the paper and encrypt it.</p>
          </div>
          {canGenerate && <Button onClick={generate} loading={busy} icon={<Sparkles size={15} />}>Generate</Button>}
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
              : lifecycle.map((state) => ({ state, at: null, reason: "", role: "" }))
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
            <div><div className="eyebrow">{uploaded ? "Examination" : "Frozen blueprint"}</div><h3>Configuration</h3></div>
            <FileLock2 size={17} className="muted" />
          </div>
          {config ? (
            <>
              <KeyValue
                items={[
                  { label: "Total marks", value: config.total_marks },
                  ...(uploaded
                    ? []
                    : [
                        { label: "Question groups", value: config.pattern.length },
                        { label: "Retry limit", value: config.retry_limit },
                      ]),
                  { label: "Frozen", value: config.frozen ? <Badge tone="success">Yes</Badge> : <Badge tone="warning">Draft</Badge> },
                  { label: "Frozen at", value: config.frozen_at ? formatDateTime(config.frozen_at) : "—" },
                  { label: "Blueprint hash", value: <code className="hash">{config.frozen_hash ? `${config.frozen_hash.slice(0, 16)}…` : "—"}</code> },
                ]}
              />
              {!uploaded && (
                <>
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
              )}
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

      {paper.data && paper.data.state !== "DISCARDED" ? (
        <>
          <Card className="secure-paper-card">
            <div className="secure-paper-head">
              <div className="secure-icon"><FileKey2 size={20} /></div>
              <div>
                <div className="eyebrow">Encrypted artifact · version {paper.data.version}</div>
                <h3>{paper.data.algorithm} · key {paper.data.key_id}</h3>
                <p>Ciphertext lives in secure object storage. The wrapped data key is stored separately from the payload nonce.</p>
              </div>
              <Badge tone={statusTone(paper.data.state)}>{titleCase(paper.data.state)}</Badge>
            </div>
            <div className="secure-stats">
              <div><span>Ciphertext</span><strong>{bytes(paper.data.ciphertext_bytes)}</strong></div>
              {uploaded ? (
                <div><span>Pages</span><strong>{paper.data.page_count}</strong></div>
              ) : (
                <div><span>Questions</span><strong>{paper.data.question_count}</strong></div>
              )}
              <div><span>Total marks</span><strong>{paper.data.total_marks}</strong></div>
              <div><span>Hash verified</span><strong>{paper.data.plaintext_hash_matches ? "Yes" : "No"}</strong></div>
            </div>
            <div className="hash-block">
              <Fingerprint size={15} />
              <div><span>SHA-256 ciphertext</span><code>{paper.data.ciphertext_sha256}</code></div>
            </div>
            <div className="alert alert-info">
              <ShieldCheck size={16} />
              {uploaded
                ? "The stored paper cannot be read back through Praxis. The HOD decrypts it in memory during the release window and prints stamped, numbered copies."
                : "Question plaintext is not available to faculty. The HOD release ceremony decrypts in memory only during the window."}
            </div>
          </Card>

          <Card>
            <div className="card-head"><div><div className="eyebrow">After sealing</div><h3>What happens next</h3></div></div>
            <div className="next-steps">
              <div><span>1</span><div><strong>HOD release window</strong><p>Opens 24 hours before the exam and closes two hours after the start time.</p></div></div>
              <div><span>2</span><div><strong>MFA-authorized decryption</strong><p>The HOD receives a short-lived release token and verifies ciphertext integrity.</p></div></div>
              <div><span>3</span><div><strong>Controlled printing</strong><p>At most three numbered prints per paper. Every decrypt and print is written to the tamper-evident audit trail.</p></div></div>
            </div>
          </Card>
        </>
      ) : (
        !uploaded && (
          <Card>
            <div className="card-head"><div><div className="eyebrow">Sealed output</div><h3>No secured paper</h3></div></div>
            <p className="muted">
              {data.status === "FAILED"
                ? "The last attempt did not produce a paper. Its partial work was discarded; retrying starts a fresh version."
                : "Once generation completes, this panel shows encryption metadata and a state-machine timeline. Question text will remain sealed."}
            </p>
          </Card>
        )
      )}

      <Modal
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Cancel this examination?"
        description="The paper is discarded and its ciphertext destroyed. This cannot be undone."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCancelOpen(false)}>Keep examination</Button>
            <Button variant="danger" loading={busy} disabled={cancelReason.trim().length < 5} onClick={cancelExam} icon={<Ban size={15} />}>
              Cancel examination
            </Button>
          </>
        }
      >
        <Field label="Reason (recorded in the audit trail)">
          <Textarea value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} rows={3} placeholder="e.g. Exam rescheduled to next week" />
        </Field>
      </Modal>
    </div>
  );
}
