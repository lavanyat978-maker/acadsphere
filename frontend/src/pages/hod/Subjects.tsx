import { useEffect, useMemo, useState } from "react";
import { BookOpen, Check, Pencil, Plus, Trash2, UserMinus, UserPlus } from "lucide-react";
import { useToast } from "../../context/ToastContext";
import { api, errorMessage, fieldErrors } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { AdminUser, EnrolledStudent, Subject, Topic } from "../../lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  PageHeader,
  PageLoading,
  Select,
  Textarea,
} from "../../components/ui";

export function Subjects() {
  const { toast } = useToast();
  const subjects = useApi<Subject[]>("/subjects");
  const teachers = useApi<AdminUser[]>("/users?role=TEACHER");
  const students = useApi<AdminUser[]>("/users?role=STUDENT");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const enrolled = useApi<EnrolledStudent[]>(selectedId ? `/subjects/${selectedId}/students` : null);

  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ code: "", name: "", description: "" });
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState({ name: "", description: "" });
  const [topicDraft, setTopicDraft] = useState({ name: "", unit: "Unit 1" });
  const [renaming, setRenaming] = useState<{ id: string; name: string; unit: string } | null>(null);
  const [teacherToAdd, setTeacherToAdd] = useState("");
  const [studentsToAdd, setStudentsToAdd] = useState<string[]>([]);
  const [enrolOpen, setEnrolOpen] = useState(false);
  const [studentQuery, setStudentQuery] = useState("");

  useEffect(() => {
    if (!selectedId && subjects.data?.length) setSelectedId(subjects.data[0].id);
  }, [subjects.data, selectedId]);

  const subject = (subjects.data ?? []).find((item) => item.id === selectedId) ?? null;
  const enrolledIds = useMemo(() => new Set((enrolled.data ?? []).map((row) => row.id)), [enrolled.data]);
  const candidates = useMemo(() => {
    const term = studentQuery.trim().toLowerCase();
    return (students.data ?? []).filter(
      (row) =>
        row.is_active &&
        !enrolledIds.has(row.id) &&
        (!term || row.full_name.toLowerCase().includes(term) || row.email.toLowerCase().includes(term)),
    );
  }, [students.data, enrolledIds, studentQuery]);

  if (subjects.loading && !subjects.data) return <PageLoading label="Loading subjects" />;
  if (subjects.error) return <ErrorState message={subjects.error} onRetry={subjects.reload} />;

  const run = async (action: () => Promise<unknown>, success: string, after?: () => void) => {
    setBusy(true);
    try {
      await action();
      toast(success, "success");
      subjects.reload();
      after?.();
    } catch (err) {
      const fields = fieldErrors(err);
      toast(fields.length ? fields.join(" ") : errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const createSubject = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    try {
      const created = await api<Subject>("/subjects", { method: "POST", body: { ...draft, teacher_ids: [] } });
      setCreating(false);
      setDraft({ code: "", name: "", description: "" });
      subjects.reload();
      setSelectedId(created.id);
      toast("Subject created. Add topics, teachers and students next.", "success");
    } catch (err) {
      const fields = fieldErrors(err);
      setFormError(fields.length ? fields.join(" ") : errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const assignedTeacherIds = new Set(subject?.teacher_ids ?? []);
  const teacherName = (id: string) => teachers.data?.find((row) => row.id === id)?.full_name ?? "Teacher";

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Administration"
        title="Subjects & enrolment"
        description="Subjects define who works where: teachers are assigned, students are enrolled, topics drive practice and exams."
        actions={
          <Button icon={<Plus size={15} />} onClick={() => { setFormError(null); setCreating(true); }}>
            New subject
          </Button>
        }
      />

      {(subjects.data ?? []).length === 0 ? (
        <Card>
          <EmptyState
            icon={<BookOpen size={22} />}
            title="No subjects yet"
            description="Create the first subject, then assign teachers and enrol students."
          />
        </Card>
      ) : (
        <div className="admin-split">
          <div className="subject-index">
            {(subjects.data ?? []).map((item) => (
              <button
                key={item.id}
                className={`subject-index-item ${item.id === selectedId ? "active" : ""}`}
                onClick={() => { setSelectedId(item.id); setEditing(false); setRenaming(null); }}
              >
                <strong>{item.code}</strong>
                <span>{item.name}</span>
                <small>
                  {item.assigned_teachers.length} teachers · {item.student_count} students · {item.topics.length} topics
                </small>
              </button>
            ))}
          </div>

          {subject && (
            <div className="stack">
              <Card>
                <div className="card-head">
                  <div>
                    <div className="eyebrow">{subject.code} · {subject.department}</div>
                    <h3>{subject.name}</h3>
                  </div>
                  {!editing && (
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<Pencil size={14} />}
                      onClick={() => { setEdit({ name: subject.name, description: subject.description }); setEditing(true); }}
                    >
                      Edit
                    </Button>
                  )}
                </div>
                {editing ? (
                  <form
                    className="stack"
                    onSubmit={(event) => {
                      event.preventDefault();
                      run(
                        () => api(`/subjects/${subject.id}`, { method: "PATCH", body: edit }),
                        "Subject updated.",
                        () => setEditing(false),
                      );
                    }}
                  >
                    <Field label="Name">
                      <Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} required minLength={3} />
                    </Field>
                    <Field label="Description">
                      <Textarea value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} rows={3} />
                    </Field>
                    <div className="inline-actions">
                      <Button type="submit" loading={busy}>Save</Button>
                      <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                    </div>
                  </form>
                ) : (
                  <p className="muted">{subject.description || "No description."}</p>
                )}
              </Card>

              <Card>
                <div className="card-head compact">
                  <div>
                    <div className="eyebrow">Topics</div>
                    <h3>{subject.topics.length} topics</h3>
                  </div>
                </div>
                <div className="admin-list">
                  {subject.topics.map((topic: Topic) =>
                    renaming?.id === topic.id ? (
                      <form
                        key={topic.id}
                        className="admin-list-row"
                        onSubmit={(event) => {
                          event.preventDefault();
                          run(
                            () => api(`/subjects/${subject.id}/topics/${topic.id}`, {
                              method: "PATCH",
                              body: { name: renaming.name, unit: renaming.unit },
                            }),
                            "Topic updated.",
                            () => setRenaming(null),
                          );
                        }}
                      >
                        <Input value={renaming.unit} onChange={(e) => setRenaming({ ...renaming, unit: e.target.value })} className="input unit-input" />
                        <Input value={renaming.name} onChange={(e) => setRenaming({ ...renaming, name: e.target.value })} required minLength={2} />
                        <button className="icon-btn" type="submit" aria-label="Save topic"><Check size={15} /></button>
                      </form>
                    ) : (
                      <div key={topic.id} className="admin-list-row">
                        <Badge>{topic.unit}</Badge>
                        <span className="admin-list-main">{topic.name}</span>
                        <button className="icon-btn" aria-label={`Rename ${topic.name}`} onClick={() => setRenaming({ id: topic.id, name: topic.name, unit: topic.unit })}>
                          <Pencil size={14} />
                        </button>
                        <button
                          className="icon-btn danger"
                          aria-label={`Delete ${topic.name}`}
                          onClick={() => {
                            if (window.confirm(`Delete topic "${topic.name}"? Topics used by tests or exams cannot be deleted.`)) {
                              run(() => api(`/subjects/${subject.id}/topics/${topic.id}`, { method: "DELETE" }), "Topic deleted.");
                            }
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ),
                  )}
                </div>
                <form
                  className="admin-add-row"
                  onSubmit={(event) => {
                    event.preventDefault();
                    run(
                      () => api(`/subjects/${subject.id}/topics`, { method: "POST", body: topicDraft }),
                      "Topic added.",
                      () => setTopicDraft({ name: "", unit: topicDraft.unit }),
                    );
                  }}
                >
                  <Input className="input unit-input" value={topicDraft.unit} onChange={(e) => setTopicDraft({ ...topicDraft, unit: e.target.value })} aria-label="Unit" />
                  <Input value={topicDraft.name} onChange={(e) => setTopicDraft({ ...topicDraft, name: e.target.value })} placeholder="New topic name" required minLength={2} />
                  <Button type="submit" variant="secondary" size="sm" icon={<Plus size={14} />} loading={busy}>Add</Button>
                </form>
              </Card>

              <div className="split-grid">
                <Card>
                  <div className="card-head compact">
                    <div>
                      <div className="eyebrow">Teachers</div>
                      <h3>{subject.teacher_ids.length} assigned</h3>
                    </div>
                  </div>
                  <div className="admin-list">
                    {subject.teacher_ids.length === 0 && <p className="muted">No teacher assigned yet.</p>}
                    {subject.teacher_ids.map((id) => (
                      <div key={id} className="admin-list-row">
                        <span className="admin-list-main">{teacherName(id)}</span>
                        <button
                          className="icon-btn danger"
                          aria-label={`Unassign ${teacherName(id)}`}
                          onClick={() => {
                            if (window.confirm(`Remove ${teacherName(id)} from ${subject.code}? Their existing exams and tests stay.`)) {
                              run(() => api(`/subjects/${subject.id}/teachers/${id}`, { method: "DELETE" }), "Teacher unassigned.");
                            }
                          }}
                        >
                          <UserMinus size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <div className="admin-add-row">
                    <Select value={teacherToAdd} onChange={(e) => setTeacherToAdd(e.target.value)} aria-label="Teacher to assign">
                      <option value="">Choose a teacher…</option>
                      {(teachers.data ?? [])
                        .filter((row) => row.is_active && !assignedTeacherIds.has(row.id))
                        .map((row) => (
                          <option key={row.id} value={row.id}>{row.full_name}</option>
                        ))}
                    </Select>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<UserPlus size={14} />}
                      disabled={!teacherToAdd}
                      loading={busy}
                      onClick={() =>
                        run(
                          () => api(`/subjects/${subject.id}/teachers/${teacherToAdd}`, { method: "POST" }),
                          "Teacher assigned.",
                          () => setTeacherToAdd(""),
                        )
                      }
                    >
                      Assign
                    </Button>
                  </div>
                </Card>

                <Card>
                  <div className="card-head compact">
                    <div>
                      <div className="eyebrow">Students</div>
                      <h3>{enrolled.data?.length ?? subject.student_count} enrolled</h3>
                    </div>
                    <Button size="sm" variant="secondary" icon={<UserPlus size={14} />} onClick={() => { setStudentsToAdd([]); setStudentQuery(""); setEnrolOpen(true); }}>
                      Enrol
                    </Button>
                  </div>
                  <div className="admin-list admin-list-scroll">
                    {(enrolled.data ?? []).length === 0 && <p className="muted">No students enrolled. They will not see this subject's practice tests.</p>}
                    {(enrolled.data ?? []).map((row) => (
                      <div key={row.id} className="admin-list-row">
                        <span className="admin-list-main">
                          {row.full_name}
                          <small>{row.email}</small>
                        </span>
                        <button
                          className="icon-btn danger"
                          aria-label={`Unenrol ${row.full_name}`}
                          onClick={() => {
                            if (window.confirm(`Unenrol ${row.full_name} from ${subject.code}?`)) {
                              run(
                                () => api(`/subjects/${subject.id}/students/${row.id}`, { method: "DELETE" }),
                                "Student unenrolled.",
                                enrolled.reload,
                              );
                            }
                          }}
                        >
                          <UserMinus size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </Card>
              </div>
            </div>
          )}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New subject"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            <Button type="submit" form="create-subject" loading={busy}>Create subject</Button>
          </>
        }
      >
        <form id="create-subject" className="form-grid" onSubmit={createSubject}>
          <Field label="Code" hint="Letters, digits and dashes, e.g. CS503">
            <Input value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} required pattern="[A-Za-z0-9\-]+" minLength={2} maxLength={20} />
          </Field>
          <Field label="Name">
            <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required minLength={3} />
          </Field>
          <Field label="Description" className="span-2">
            <Textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} rows={3} />
          </Field>
          {formError && <div className="form-error span-2">{formError}</div>}
        </form>
      </Modal>

      <Modal
        open={enrolOpen}
        onClose={() => setEnrolOpen(false)}
        title={`Enrol students in ${subject?.code ?? ""}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEnrolOpen(false)}>Cancel</Button>
            <Button
              disabled={studentsToAdd.length === 0 || !subject}
              loading={busy}
              onClick={() =>
                subject &&
                run(
                  () => api(`/subjects/${subject.id}/students`, { method: "POST", body: { student_ids: studentsToAdd } }),
                  `${studentsToAdd.length} student(s) enrolled.`,
                  () => { setEnrolOpen(false); enrolled.reload(); },
                )
              }
            >
              Enrol {studentsToAdd.length || ""}
            </Button>
          </>
        }
      >
        <div className="stack">
          <Input placeholder="Search students" value={studentQuery} onChange={(e) => setStudentQuery(e.target.value)} />
          <div className="check-grid check-grid-scroll">
            {candidates.length === 0 && <p className="muted">Every active student is already enrolled.</p>}
            {candidates.map((row) => (
              <label key={row.id} className="checkbox-row">
                <input
                  type="checkbox"
                  checked={studentsToAdd.includes(row.id)}
                  onChange={() =>
                    setStudentsToAdd((current) =>
                      current.includes(row.id) ? current.filter((id) => id !== row.id) : [...current, row.id],
                    )
                  }
                />
                {row.full_name} <span className="muted">· {row.email}</span>
              </label>
            ))}
          </div>
          {candidates.length > 0 && (
            <button className="text-link" type="button" onClick={() => setStudentsToAdd(candidates.map((row) => row.id))}>
              Select all {candidates.length}
            </button>
          )}
        </div>
      </Modal>
    </div>
  );
}
