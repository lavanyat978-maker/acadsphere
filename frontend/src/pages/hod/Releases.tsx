import { useMemo, useState } from "react";
import {
  CheckCircle2,
  Clock3,
  Download,
  FileKey2,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  Printer,
  ScanLine,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { api, downloadPdf, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { ReleaseQueueItem } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Modal, PageHeader, PageLoading, Textarea } from "../../components/ui";
import { formatDateTime, relativeTime, statusTone, titleCase } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

interface TokenState {
  token: string;
  expiresAt: string;
}

export function Releases() {
  const queue = useApi<ReleaseQueueItem[]>("/exams/release/queue");
  const { toast } = useToast();
  const [selected, setSelected] = useState<ReleaseQueueItem | null>(null);
  const [reason, setReason] = useState("Scheduled internal assessment release");
  const [token, setToken] = useState<TokenState | null>(null);
  const [decrypted, setDecrypted] = useState(false);
  const [printed, setPrinted] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const items = useMemo(
    () => (queue.data ?? []).slice().sort((a, b) => new Date(a.exam_date).getTime() - new Date(b.exam_date).getTime()),
    [queue.data],
  );

  const open = (item: ReleaseQueueItem) => {
    setSelected(item);
    setReason(item.release?.reason || "Scheduled internal assessment release");
    setToken(null);
    setDecrypted(false);
    setPrinted((item.release?.print_count ?? 0) > 0);
  };

  const authorize = async () => {
    if (!selected) return;
    setBusy("authorize");
    try {
      const result = await api<{ release_token: string; expires_at: string }>(
        `/exams/release/${selected.paper_id}/authorize`,
        { method: "POST", body: { reason } },
      );
      setToken({ token: result.release_token, expiresAt: result.expires_at });
      toast("Release authorized. Token held in memory for 10 minutes.", "success");
      queue.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  };

  const decrypt = async () => {
    if (!selected || !token) return;
    setBusy("decrypt");
    try {
      const result = await api<{ integrity_verified: boolean; question_count: number; sections: Array<{ label: string }> }>(
        `/exams/release/${selected.paper_id}/decrypt`,
        { method: "POST", body: { release_token: token.token, reason } },
      );
      if (!result.integrity_verified) throw new Error("Integrity verification failed.");
      setDecrypted(true);
      toast(`Integrity verified for ${result.question_count} questions.`, "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  };

  const print = async () => {
    if (!selected || !token) return;
    setBusy("print");
    try {
      await downloadPdf(`/exams/release/${selected.paper_id}/pdf?release_token=${encodeURIComponent(token.token)}`);
      setPrinted(true);
      toast("Printable copy generated and downloaded.", "success");
      queue.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  };

  const release = async () => {
    if (!selected) return;
    setBusy("release");
    try {
      await api(`/exams/release/${selected.paper_id}/released`, { method: "POST" });
      toast("Paper marked released. Handover recorded.", "success");
      setSelected(null);
      queue.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  };

  if (queue.loading && !queue.data) return <PageLoading label="Reading the release queue" />;
  if (queue.error) return <ErrorState message={queue.error} onRetry={queue.reload} />;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Controlled release"
        title="Release control"
        description="Decryption is time-windowed and MFA-gated. Every action is written to the hash-chained audit trail."
        actions={<Badge tone="warning"><LockKeyhole size={12} /> Plaintext never stored</Badge>}
      />

      <Card className="release-policy">
        <div><ShieldCheck size={18} /><span>Window opens 24h before the exam</span></div>
        <div><Clock3 size={18} /><span>Closes 2h after the start time</span></div>
        <div><Printer size={18} /><span>Maximum three controlled prints</span></div>
        <div><KeyRound size={18} /><span>Release tokens expire in 10 minutes</span></div>
      </Card>

      {items.length ? (
        <div className="release-list">
          {items.map((item) => {
            const released = item.paper_state === "RELEASED" || item.release?.status === "RELEASED";
            const printCount = item.release?.print_count ?? 0;
            return (
              <Card className={`release-card ${released ? "released" : ""}`} key={item.paper_id} as="article">
                <div className="release-main">
                  <div className="release-top">
                    <div className="release-date">
                      <strong>{new Date(item.exam_date).getDate()}</strong>
                      <span>{new Date(item.exam_date).toLocaleDateString(undefined, { month: "short" })}</span>
                    </div>
                    <div>
                      <h3>{item.exam_title}</h3>
                      <p>{item.subject_name} · {item.exam_type} · {item.total_marks} marks · {item.venue}</p>
                    </div>
                    <div className="release-badges">
                      <Badge tone={statusTone(item.paper_state)}>{titleCase(item.paper_state)}</Badge>
                      <Badge tone={released ? "success" : item.window_open ? "warning" : "neutral"} dot>
                        {released ? "Released" : item.window_open ? "Window open" : "Window closed"}
                      </Badge>
                    </div>
                  </div>
                  <div className="release-meta">
                    <span><Clock3 size={14} /> Exam {formatDateTime(item.exam_date)} ({relativeTime(item.exam_date)})</span>
                    <span><ShieldCheck size={14} /> Window closes {formatDateTime(item.window_closes_at)}</span>
                    <span><Printer size={14} /> {printCount} / 3 prints</span>
                    {item.release && <span><KeyRound size={14} /> Authorized by {item.release.authorized_by_name}</span>}
                  </div>
                  {item.release && (
                    <div className="release-reason">
                      <span>Reason</span> {item.release.reason}
                    </div>
                  )}
                </div>
                <div className="release-actions">
                  {released ? (
                    <div className="released-stamp"><CheckCircle2 size={18} /> Handover complete</div>
                  ) : (
                    <Button
                      variant={item.window_open ? "primary" : "secondary"}
                      disabled={!item.window_open}
                      onClick={() => open(item)}
                      icon={<ScanLine size={16} />}
                    >
                      {item.window_open ? "Manage release" : "Window closed"}
                    </Button>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <Card>
          <EmptyState
            icon={<FileKey2 size={22} />}
            title="No sealed papers in the queue"
            description="Papers appear here after faculty generation completes and the encrypted artifact is stored."
          />
        </Card>
      )}

      <Modal
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.exam_title ?? "Release"}
        description={selected ? `${selected.subject_name} · exam ${formatDateTime(selected.exam_date)}` : undefined}
        width="720px"
        footer={
          <>
            <Button variant="ghost" onClick={() => setSelected(null)}>Close</Button>
            {!token && (
              <Button loading={busy === "authorize"} onClick={authorize} icon={<KeyRound size={15} />} disabled={!selected?.window_open}>
                Authorize &amp; issue token
              </Button>
            )}
            {token && !decrypted && (
              <Button loading={busy === "decrypt"} onClick={decrypt} icon={<Fingerprint size={15} />}>
                Decrypt &amp; verify integrity
              </Button>
            )}
            {token && decrypted && (
              <Button loading={busy === "print"} onClick={print} icon={<Download size={15} />}>
                Generate printable PDF
              </Button>
            )}
            {token && decrypted && (
              <Button loading={busy === "release"} onClick={release} variant="secondary" disabled={!printed} icon={<CheckCircle2 size={15} />}>
                Mark released
              </Button>
            )}
          </>
        }
      >
        <div className="ceremony">
          <div className={`ceremony-step ${token ? "done" : "active"}`}>
            <span className="ceremony-number">{token ? <CheckCircle2 size={16} /> : "1"}</span>
            <div>
              <strong>Authorization &amp; reason</strong>
              <p>The reason is written to the audit trail. A 10-minute single-session token is issued.</p>
              {!token ? (
                <Field label="Release reason" hint="At least 5 characters. Be specific — this is audited.">
                  <Textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={255} />
                </Field>
              ) : (
                <div className="token-chip">
                  <KeyRound size={14} /> Token held in memory · expires {formatDateTime(token.expiresAt)}
                </div>
              )}
            </div>
          </div>

          <div className={`ceremony-step ${decrypted ? "done" : token ? "active" : ""}`}>
            <span className="ceremony-number">{decrypted ? <CheckCircle2 size={16} /> : "2"}</span>
            <div>
              <strong>Decrypt &amp; verify</strong>
              <p>Both ciphertext SHA-256 and plaintext SHA-256 must match before any question is rendered.</p>
              {decrypted && <Badge tone="success"><Fingerprint size={12} /> Integrity verified</Badge>}
            </div>
          </div>

          <div className={`ceremony-step ${decrypted ? "active" : ""}`}>
            <span className="ceremony-number">3</span>
            <div>
              <strong>Controlled print</strong>
              <p>The PDF is generated in memory. Each download increments the server-side print counter (max 3).</p>
              {selected?.release && (
                <span className="muted">{selected.release.print_count} of 3 prints used</span>
              )}
            </div>
          </div>

          <div className="ceremony-step">
            <span className="ceremony-number">4</span>
            <div>
              <strong>Mark released</strong>
              <p>Closes the paper lifecycle after physical handover. This action is irreversible.</p>
            </div>
          </div>

          {!selected?.window_open && (
            <div className="alert alert-danger">
              <XCircle size={16} /> The release window is closed. Authorization is disabled by the server.
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
