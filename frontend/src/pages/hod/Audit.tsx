import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, ClipboardList, Fingerprint, ShieldCheck } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { AuditPage } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, PageLoading, Select, Spinner } from "../../components/ui";
import { formatDateTime, titleCase } from "../../lib/format";
import { useToast } from "../../context/ToastContext";

const PAGE_SIZE = 25;

export function Audit() {
  const [event, setEvent] = useState("");
  const [success, setSuccess] = useState("ALL");
  const [offset, setOffset] = useState(0);
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState<{ verified: boolean; records_checked: number; broken_at_seq: number | null; head_hash: string } | null>(null);
  const { toast } = useToast();

  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (event.trim()) query.set("event", event.trim().toUpperCase());
  if (success !== "ALL") query.set("success", String(success === "SUCCESS"));

  const page = useApi<AuditPage>(`/audit/logs?${query.toString()}`);
  const summary = useApi<{ window_days: number; total_events: number; denied_events: number; top_events: Array<[string, number]> }>("/audit/summary?days=30");

  const verify = async () => {
    setVerifying(true);
    try {
      const result = await api<{ verified: boolean; records_checked: number; broken_at_seq: number | null; head_hash: string }>("/audit/verify");
      setVerification(result);
      toast(result.verified ? "Hash chain verified." : `Chain broken at record ${result.broken_at_seq}.`, result.verified ? "success" : "error");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Append-only trail"
        title="Audit trail"
        description="Every authentication, authorization, generation and release event. Records are hash-chained; edits are detectable."
        actions={
          <Button onClick={verify} loading={verifying} icon={<Fingerprint size={16} />}>
            Verify hash chain
          </Button>
        }
      />

      {verification && (
        <Card className={`verify-banner ${verification.verified ? "verified" : "broken"}`}>
          {verification.verified ? <ShieldCheck size={20} /> : <AlertTriangle size={20} />}
          <div>
            <strong>{verification.verified ? "Chain verified" : `Tampering detected at record ${verification.broken_at_seq}`}</strong>
            <p>
              {verification.records_checked} records checked · head hash <code>{verification.head_hash.slice(0, 20)}…</code>
            </p>
          </div>
        </Card>
      )}

      <div className="stat-grid stat-grid-tight">
        <div className="stat"><span className="stat-label">Events · 30 days</span><div className="stat-value">{summary.data?.total_events ?? "—"}</div></div>
        <div className="stat"><span className="stat-label">Denied actions</span><div className="stat-value">{summary.data?.denied_events ?? "—"}</div></div>
        <div className="stat stat-wide">
          <span className="stat-label">Most frequent</span>
          <div className="event-chips">
            {(summary.data?.top_events ?? []).slice(0, 4).map(([name, count]) => (
              <span key={name} className="event-chip">{titleCase(name)} <strong>{count}</strong></span>
            ))}
          </div>
        </div>
      </div>

      <Card className="filter-bar">
        <div className="search-wrap">
          <ClipboardList size={16} />
          <input
            className="search-input"
            placeholder="Filter exact event, e.g. PAPER_DECRYPTED"
            value={event}
            onChange={(inputEvent) => { setEvent(inputEvent.target.value); setOffset(0); }}
          />
        </div>
        <div className="filter-group">
          <Select className="compact" value={success} onChange={(selectEvent) => { setSuccess(selectEvent.target.value); setOffset(0); }}>
            <option value="ALL">All outcomes</option>
            <option value="SUCCESS">Successful</option>
            <option value="DENIED">Denied / failed</option>
          </Select>
        </div>
      </Card>

      {page.loading && !page.data ? (
        <PageLoading label="Reading audit records" />
      ) : page.error ? (
        <ErrorState message={page.error} onRetry={page.reload} />
      ) : page.data?.items.length ? (
        <Card>
          <div className="table-wrap">
            <table className="data-table audit-table">
              <thead>
                <tr>
                  <th>Seq</th>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>Resource</th>
                  <th>Outcome</th>
                  <th>When</th>
                  <th>Hash</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((row) => (
                  <tr key={row.seq}>
                    <td className="muted">#{row.seq}</td>
                    <td>
                      <div className="table-primary">
                        <strong>{titleCase(row.event)}</strong>
                        {Object.keys(row.metadata_json).length > 0 && (
                          <details className="metadata-details">
                            <summary>metadata</summary>
                            <pre>{JSON.stringify(row.metadata_json, null, 2)}</pre>
                          </details>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="table-primary">
                        <strong>{row.actor_email || "anonymous"}</strong>
                        <span>{row.actor_role}</span>
                      </div>
                    </td>
                    <td className="muted">{row.resource_type}{row.resource_id ? ` · ${row.resource_id.slice(0, 8)}` : ""}</td>
                    <td>
                      <Badge tone={row.success ? "success" : "danger"}>
                        {row.success ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
                        {row.success ? "Success" : "Denied"}
                      </Badge>
                    </td>
                    <td className="muted">{formatDateTime(row.created_at)}</td>
                    <td><code className="hash">{row.record_hash.slice(0, 10)}…</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, page.data.total)} of {page.data.total}
            </span>
            <div className="button-row">
              <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} icon={<ChevronLeft size={15} />}>Previous</Button>
              <Button variant="secondary" size="sm" disabled={offset + PAGE_SIZE >= page.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next <ChevronRight size={15} /></Button>
            </div>
          </div>
        </Card>
      ) : (
        <Card>
          <EmptyState icon={<ClipboardList size={22} />} title="No audit records match" description="Clear the event filter or widen the outcome selection." />
        </Card>
      )}

      {(page.loading || verifying) && page.data && <div className="floating-status"><Spinner label="Refreshing" /></div>}
    </div>
  );
}
