import { useMemo, useState } from "react";
import { AlertTriangle, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { SecurityEvent } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, PageLoading, Select, Stat } from "../../components/ui";
import { useToast } from "../../context/ToastContext";
import { formatDateTime, relativeTime, titleCase } from "../../lib/format";

const ICONS: Record<string, typeof ShieldAlert> = {
  CRITICAL: AlertTriangle,
  WARNING: ShieldAlert,
  INFO: ShieldCheck,
};

export function Security() {
  const events = useApi<SecurityEvent[]>("/audit/security-events?limit=100");
  const [severity, setSeverity] = useState("ALL");
  const [resolving, setResolving] = useState<string | null>(null);
  const { toast } = useToast();

  const resolve = async (id: string) => {
    setResolving(id);
    try {
      await api(`/audit/security-events/${id}/resolve`, { method: "POST" });
      events.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setResolving(null);
    }
  };

  const filtered = useMemo(
    () => (events.data ?? []).filter((event) => severity === "ALL" || event.severity === severity),
    [events.data, severity],
  );

  if (events.loading && !events.data) return <PageLoading label="Loading security signals" />;
  if (events.error) return <ErrorState message={events.error} onRetry={events.reload} />;

  const all = events.data ?? [];
  const critical = all.filter((event) => event.severity === "CRITICAL").length;
  const warnings = all.filter((event) => event.severity === "WARNING").length;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Control plane"
        title="Security events"
        description="Release attempts outside the window, decryptions, prints, MFA failures and unauthorized access attempts."
        actions={
          <Select value={severity} onChange={(event) => setSeverity(event.target.value)} className="compact">
            <option value="ALL">All severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
          </Select>
        }
      />

      <div className="stat-grid stat-grid-tight">
        <Stat label="Signals" value={all.length} hint="Most recent 100" icon={<ShieldQuestion size={17} />} />
        <Stat label="Warnings" value={warnings} tone="warning" icon={<ShieldAlert size={17} />} />
        <Stat label="Critical" value={critical} tone="danger" icon={<AlertTriangle size={17} />} />
        <Stat label="Info" value={all.length - warnings - critical} tone="success" icon={<ShieldCheck size={17} />} />
      </div>

      {filtered.length ? (
        <div className="security-timeline">
          {filtered.map((event) => {
            const Icon = ICONS[event.severity] ?? ShieldQuestion;
            return (
              <Card className={`security-event severity-${event.severity.toLowerCase()}`} key={event.id} as="article">
                <div className="security-event-icon"><Icon size={18} /></div>
                <div className="security-event-main">
                  <div className="security-event-head">
                    <strong>{titleCase(event.event_type)}</strong>
                    <Badge tone={event.severity === "CRITICAL" ? "danger" : event.severity === "WARNING" ? "warning" : "success"}>
                      {event.severity}
                    </Badge>
                    {event.resolved ? (
                      <Badge tone="neutral">Resolved</Badge>
                    ) : event.severity !== "INFO" ? (
                      <Button size="sm" variant="ghost" loading={resolving === event.id} onClick={() => resolve(event.id)}>
                        Mark resolved
                      </Button>
                    ) : null}
                  </div>
                  <p>{event.summary}</p>
                  <div className="security-event-meta">
                    <span>{relativeTime(event.created_at)}</span>
                    <span>{formatDateTime(event.created_at)}</span>
                    {event.ip_address && <span>IP {event.ip_address}</span>}
                  </div>
                  {Object.keys(event.detail).length > 0 && (
                    <details className="metadata-details">
                      <summary>Event detail</summary>
                      <pre>{JSON.stringify(event.detail, null, 2)}</pre>
                    </details>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <Card>
          <EmptyState icon={<ShieldCheck size={22} />} title="No security events in this view" description="The control plane is quiet for the selected severity." />
        </Card>
      )}
    </div>
  );
}
