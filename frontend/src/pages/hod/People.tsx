import { useMemo, useRef, useState } from "react";
import { Check, Copy, Download, FileUp, KeyRound, Lock, Plus, Search, ShieldOff, UserCheck, UserX } from "lucide-react";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { api, errorMessage, fieldErrors } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { formatDateTime, initials, titleCase } from "../../lib/format";
import type { AdminUser, Role, Subject } from "../../lib/types";
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  PageHeader,
  PageLoading,
  Select,
} from "../../components/ui";

type RoleFilter = "ALL" | Role;

interface ImportedRow {
  email: string;
  full_name: string;
  role: string;
  temporary_password: string;
}

const CSV_TEMPLATE = "email,full_name,role,department,subjects\nstudent.one@college.edu,Student One,STUDENT,Computer Science,CS501;CS502\nteacher.one@college.edu,Teacher One,TEACHER,Computer Science,CS501\n";

function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

const csvCell = (value: string) => (/[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value);

interface CreatedSecret {
  title: string;
  email: string;
  password: string;
}

function CopyableSecret({ secret }: { secret: CreatedSecret }) {
  const { toast } = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret.password);
      toast("Temporary password copied.", "success");
    } catch {
      toast("Copy failed — select the password and copy it manually.", "error");
    }
  };
  return (
    <div className="stack">
      <p className="muted">
        Share this one-time password with <strong>{secret.email}</strong> through a trusted channel. It is shown
        only now; the user must replace it at first sign-in.
      </p>
      <div className="enroll-secret">
        <span className="field-label">Temporary password</span>
        <code>{secret.password}</code>
      </div>
      <Button variant="secondary" icon={<Copy size={15} />} onClick={copy}>
        Copy password
      </Button>
    </div>
  );
}

export function People() {
  const { user: me } = useAuth();
  const { toast } = useToast();
  const users = useApi<AdminUser[]>("/users");
  const subjects = useApi<Subject[]>("/subjects");
  const [filter, setFilter] = useState<RoleFilter>("ALL");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<CreatedSecret | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [form, setForm] = useState({ full_name: "", email: "", role: "STUDENT" as Role, department: "Computer Science" });
  const [formSubjects, setFormSubjects] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // CSV import: all rows are validated server-side before any account is created.
  const csvInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState<ImportedRow[] | null>(null);
  const [importErrors, setImportErrors] = useState<Array<{ row: number; error: string }> | null>(null);

  const subjectName = useMemo(() => {
    const map = new Map<string, string>();
    for (const subject of subjects.data ?? []) map.set(subject.id, subject.code);
    return map;
  }, [subjects.data]);

  const rows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return (users.data ?? []).filter(
      (row) =>
        (filter === "ALL" || row.role === filter) &&
        (!term || row.full_name.toLowerCase().includes(term) || row.email.toLowerCase().includes(term)),
    );
  }, [users.data, filter, query]);

  const counts = useMemo(() => {
    const result: Record<RoleFilter, number> = { ALL: 0, STUDENT: 0, TEACHER: 0, HOD: 0 };
    for (const row of users.data ?? []) {
      result.ALL += 1;
      result[row.role] += 1;
    }
    return result;
  }, [users.data]);

  if (users.loading && !users.data) return <PageLoading label="Loading people" />;
  if (users.error) return <ErrorState message={users.error} onRetry={users.reload} />;

  const openCreate = () => {
    setForm({ full_name: "", email: "", role: filter === "ALL" ? "STUDENT" : filter, department: "Computer Science" });
    setFormSubjects([]);
    setFormError(null);
    setCreating(true);
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setFormError(null);
    try {
      const result = await api<{ user: AdminUser; temporary_password: string | null }>("/users", {
        method: "POST",
        body: { ...form, subject_ids: form.role === "HOD" ? [] : formSubjects },
      });
      setCreating(false);
      users.reload();
      subjects.reload();
      if (result.temporary_password) {
        setSecret({ title: "Account created", email: result.user.email, password: result.temporary_password });
      }
    } catch (err) {
      const fields = fieldErrors(err);
      setFormError(fields.length ? fields.join(" ") : errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const act = async (row: AdminUser, action: () => Promise<void>) => {
    setBusyId(row.id);
    try {
      await action();
      users.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusyId(null);
    }
  };

  const resetPassword = (row: AdminUser) =>
    act(row, async () => {
      if (!window.confirm(`Reset the password for ${row.full_name}? Their current sessions end immediately.`)) return;
      const result = await api<{ temporary_password: string }>(`/users/${row.id}/reset-password`, { method: "POST" });
      setSecret({ title: "Password reset", email: row.email, password: result.temporary_password });
    });

  const resetMfa = (row: AdminUser) =>
    act(row, async () => {
      if (!window.confirm(`Reset the authenticator for ${row.full_name}? They will enrol a new one.`)) return;
      await api(`/users/${row.id}/reset-mfa`, { method: "POST" });
      toast("Authenticator reset.", "success");
    });

  const toggleActive = (row: AdminUser) =>
    act(row, async () => {
      const verb = row.is_active ? "Deactivate" : "Reactivate";
      if (!window.confirm(`${verb} ${row.full_name}?`)) return;
      await api(`/users/${row.id}`, { method: "PATCH", body: { is_active: !row.is_active } });
      toast(`${row.full_name} ${row.is_active ? "deactivated" : "reactivated"}.`, "success");
    });

  const unlock = (row: AdminUser) =>
    act(row, async () => {
      await api(`/users/${row.id}/unlock`, { method: "POST" });
      toast("Lockout cleared.", "success");
    });

  const importCsv = async (file: File | undefined) => {
    if (!file) return;
    setImporting(true);
    setImportErrors(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api<{ created: ImportedRow[]; count: number }>("/users/import", { method: "POST", form });
      setImported(result.created);
      users.reload();
      subjects.reload();
      toast(`${result.count} account(s) created.`, "success");
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.details)) {
        setImportErrors(err.details as Array<{ row: number; error: string }>);
      } else {
        toast(errorMessage(err), "error");
      }
    } finally {
      setImporting(false);
      if (csvInput.current) csvInput.current.value = "";
    }
  };

  const downloadCredentials = () => {
    if (!imported) return;
    const lines = ["email,full_name,role,temporary_password", ...imported.map((row) =>
      [row.email, row.full_name, row.role, row.temporary_password].map(csvCell).join(","),
    )];
    downloadText("praxis-new-accounts.csv", lines.join("\n") + "\n");
  };

  const toggleSubject = (id: string) =>
    setFormSubjects((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Administration"
        title="People"
        description="Create accounts, reset credentials and control access. Accounts start with a one-time password."
        actions={
          <div className="page-actions-inline">
            <input
              ref={csvInput}
              type="file"
              accept=".csv,text/csv"
              className="hidden-input"
              onChange={(event) => importCsv(event.target.files?.[0])}
            />
            <Button variant="ghost" size="sm" icon={<Download size={14} />} onClick={() => downloadText("praxis-import-template.csv", CSV_TEMPLATE)}>
              CSV template
            </Button>
            <Button variant="secondary" icon={<FileUp size={15} />} loading={importing} onClick={() => csvInput.current?.click()}>
              Import CSV
            </Button>
            <Button icon={<Plus size={15} />} onClick={openCreate}>
              Add person
            </Button>
          </div>
        }
      />

      {importErrors && (
        <div className="form-error">
          <strong>Nothing was imported. Fix these rows and upload the file again:</strong>
          <ul className="error-list">
            {importErrors.map((item) => (
              <li key={`${item.row}-${item.error}`}>Row {item.row}: {item.error}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="toolbar-row">
        <div className="tabs">
          {(["ALL", "STUDENT", "TEACHER", "HOD"] as RoleFilter[]).map((value) => (
            <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>
              {value === "ALL" ? "Everyone" : titleCase(value === "HOD" ? "HOD" : `${value}s`)}
              <span className="tab-count">{counts[value]}</span>
            </button>
          ))}
        </div>
        <div className="search-inline">
          <Search size={15} />
          <input
            className="input"
            placeholder="Search name or email"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      <div className="table-card">
        {rows.length === 0 ? (
          <EmptyState title="No matching people" description="Adjust the filter or add a new account." />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Role</th>
                  <th>Subjects</th>
                  <th>Security</th>
                  <th>Last sign-in</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className={row.is_active ? "" : "row-muted"}>
                    <td>
                      <div className="person-cell">
                        <span className={`avatar avatar-${row.role.toLowerCase()}`}>{initials(row.full_name)}</span>
                        <div className="table-primary">
                          <strong>{row.full_name}</strong>
                          <span>{row.email}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Badge tone={row.role === "HOD" ? "accent" : row.role === "TEACHER" ? "info" : "neutral"}>
                        {row.role === "HOD" ? "HOD" : titleCase(row.role)}
                      </Badge>
                    </td>
                    <td>
                      {row.role === "HOD" ? (
                        <span className="muted">All</span>
                      ) : row.subject_ids.length ? (
                        <div className="table-inline">
                          {row.subject_ids.map((id) => (
                            <span key={id}>{subjectName.get(id) ?? "—"}</span>
                          ))}
                        </div>
                      ) : (
                        <span className="muted">None</span>
                      )}
                    </td>
                    <td>
                      <div className="table-inline">
                        {!row.is_active && <Badge tone="danger">Inactive</Badge>}
                        {row.must_change_password && <Badge tone="warning">Temp password</Badge>}
                        {row.mfa_enabled ? <Badge tone="success">MFA</Badge> : <span className="muted">No MFA</span>}
                      </div>
                    </td>
                    <td className="muted">{row.last_login_at ? formatDateTime(row.last_login_at) : "Never"}</td>
                    <td>
                      {row.id !== me?.id && (
                        <div className="row-actions">
                          <button
                            className="icon-btn"
                            title="Reset password"
                            aria-label={`Reset password for ${row.full_name}`}
                            disabled={busyId === row.id}
                            onClick={() => resetPassword(row)}
                          >
                            <KeyRound size={15} />
                          </button>
                          {row.mfa_enabled && (
                            <button
                              className="icon-btn"
                              title="Reset authenticator"
                              aria-label={`Reset authenticator for ${row.full_name}`}
                              disabled={busyId === row.id}
                              onClick={() => resetMfa(row)}
                            >
                              <ShieldOff size={15} />
                            </button>
                          )}
                          <button
                            className="icon-btn"
                            title="Clear lockout"
                            aria-label={`Clear lockout for ${row.full_name}`}
                            disabled={busyId === row.id}
                            onClick={() => unlock(row)}
                          >
                            <Lock size={15} />
                          </button>
                          <button
                            className={`icon-btn ${row.is_active ? "danger" : ""}`}
                            title={row.is_active ? "Deactivate" : "Reactivate"}
                            aria-label={`${row.is_active ? "Deactivate" : "Reactivate"} ${row.full_name}`}
                            disabled={busyId === row.id}
                            onClick={() => toggleActive(row)}
                          >
                            {row.is_active ? <UserX size={15} /> : <UserCheck size={15} />}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="Add a person"
        description="A one-time password is generated and shown once after creation."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button type="submit" form="create-user" loading={saving} icon={<Plus size={15} />}>
              Create account
            </Button>
          </>
        }
      >
        <form id="create-user" className="form-grid" onSubmit={create}>
          <Field label="Full name">
            <Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required minLength={2} />
          </Field>
          <Field label="Email">
            <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </Field>
          <Field label="Role">
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              <option value="STUDENT">Student</option>
              <option value="TEACHER">Teacher</option>
              <option value="HOD">Head of Department</option>
            </Select>
          </Field>
          <Field label="Department">
            <Input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} />
          </Field>
          {form.role !== "HOD" && (
            <div className="span-2">
              <span className="field-label">{form.role === "TEACHER" ? "Assign to subjects" : "Enrol in subjects"}</span>
              <div className="check-grid">
                {(subjects.data ?? []).map((subject) => (
                  <label key={subject.id} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={formSubjects.includes(subject.id)}
                      onChange={() => toggleSubject(subject.id)}
                    />
                    {subject.code} · {subject.name}
                  </label>
                ))}
              </div>
            </div>
          )}
          {form.role === "HOD" && (
            <p className="span-2 muted">
              The new HOD enrols an authenticator app during their first sign-in.
            </p>
          )}
          {formError && <div className="form-error span-2">{formError}</div>}
        </form>
      </Modal>

      <Modal
        open={imported !== null}
        onClose={() => setImported(null)}
        title={`${imported?.length ?? 0} account(s) imported`}
        description="Download the one-time passwords now; they are not shown again. Every user must set a new password at first sign-in."
        width="720px"
        footer={
          <>
            <Button variant="secondary" icon={<Download size={15} />} onClick={downloadCredentials}>
              Download credentials CSV
            </Button>
            <Button icon={<Check size={15} />} onClick={() => setImported(null)}>
              Done
            </Button>
          </>
        }
      >
        <div className="table-wrap import-results">
          <table className="data-table data-table-compact">
            <thead>
              <tr><th>Name</th><th>Email</th><th>Role</th><th>Temporary password</th></tr>
            </thead>
            <tbody>
              {(imported ?? []).map((row) => (
                <tr key={row.email}>
                  <td>{row.full_name}</td>
                  <td>{row.email}</td>
                  <td>{titleCase(row.role)}</td>
                  <td><code>{row.temporary_password}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Modal>

      <Modal
        open={secret !== null}
        onClose={() => setSecret(null)}
        title={secret?.title ?? ""}
        footer={
          <Button icon={<Check size={15} />} onClick={() => setSecret(null)}>
            Done
          </Button>
        }
      >
        {secret && <CopyableSecret secret={secret} />}
      </Modal>
    </div>
  );
}
