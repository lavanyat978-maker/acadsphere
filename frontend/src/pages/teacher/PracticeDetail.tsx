import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BarChart3,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  Eye,
  Pencil,
  Plus,
  Send,
  Sparkles,
  Target,
  Trash2,
  Users,
} from "lucide-react";
import { api, errorMessage, fieldErrors } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { PracticeAnalytics, PracticeQuestionCreatePayload, PracticeTestDetail, Subject } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, KeyValue, Modal, PageHeader, PageLoading, Select, Stat, Textarea } from "../../components/ui";
import { difficultyTone, formatDateTime, scoreTone, statusTone, titleCase } from "../../lib/format";
import { TopicBars } from "../../components/charts";
import { useToast } from "../../context/ToastContext";

export function PracticeDetail() {
  const { testId = "" } = useParams();
  const test = useApi<PracticeTestDetail>(`/practice/staff/tests/${testId}`);
  const analytics = useApi<PracticeAnalytics>(`/practice/tests/${testId}/analytics`);
  const subjects = useApi<Subject[]>("/subjects");
  const { toast } = useToast();
  const [tab, setTab] = useState<"questions" | "analytics">("questions");
  const [generateOpen, setGenerateOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  // When set, the question modal edits this question instead of adding a new one.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [details, setDetails] = useState({ title: "", description: "", duration_minutes: 30 });
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(5);
  const [genDifficulty, setGenDifficulty] = useState("MEDIUM");
  const [genTopic, setGenTopic] = useState("");
  const [replace, setReplace] = useState(false);

  const [questionText, setQuestionText] = useState("");
  const [topicId, setTopicId] = useState("");
  const [options, setOptions] = useState([
    { id: "a", text: "" },
    { id: "b", text: "" },
    { id: "c", text: "" },
    { id: "d", text: "" },
  ]);
  const [correct, setCorrect] = useState("a");
  const [explanation, setExplanation] = useState("");
  const [marks, setMarks] = useState(1);
  const [questionDifficulty, setQuestionDifficulty] = useState("MEDIUM");

  if (test.loading && !test.data) return <PageLoading label="Opening practice set" />;
  if (test.error) return <ErrorState message={test.error} onRetry={test.reload} />;
  if (!test.data) return null;
  const data = test.data;
  const subject = (subjects.data ?? []).find((item) => item.id === data.subject_id);
  const topics = subject?.topics ?? [];

  const setStatus = async (status: string) => {
    setBusy(true);
    try {
      await api(`/practice/tests/${data.id}/status`, { method: "POST", body: { status } });
      toast(`Practice set marked ${status.toLowerCase()}.`, "success");
      test.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    try {
      const result = await api<PracticeTestDetail>(`/practice/tests/${data.id}/generate`, {
        method: "POST",
        body: {
          count,
          difficulty: genDifficulty,
          topic_id: genTopic || null,
          replace_existing: replace,
        },
      });
      toast(result._meta?.warning ?? `${result._meta?.generated ?? 0} questions generated.`, "info");
      setGenerateOpen(false);
      test.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const deleteQuestion = async (questionId: string) => {
    if (!window.confirm("Delete this question from the draft?")) return;
    setBusy(true);
    try {
      await api(`/practice/tests/${data.id}/questions/${questionId}`, { method: "DELETE" });
      toast("Question deleted.", "success");
      test.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const editable = data.can_edit;
  const isDraft = data.status === "DRAFT";
  const untouched = data.attempt_count === 0;

  const openAdd = () => {
    setEditingId(null);
    setQuestionText("");
    setTopicId("");
    setOptions([
      { id: "a", text: "" },
      { id: "b", text: "" },
      { id: "c", text: "" },
      { id: "d", text: "" },
    ]);
    setCorrect("a");
    setExplanation("");
    setMarks(1);
    setQuestionDifficulty("MEDIUM");
    setAddOpen(true);
  };

  const openEdit = (question: (typeof data.questions)[number]) => {
    setEditingId(question.id);
    setQuestionText(question.question_text);
    setTopicId(question.topic_id);
    const padded = [...question.options];
    for (const id of ["a", "b", "c", "d"]) {
      if (padded.length < 4 && !padded.some((option) => option.id === id)) padded.push({ id, text: "" });
    }
    setOptions(padded.slice(0, Math.max(4, question.options.length)));
    setCorrect(question.correct_answers?.[0] ?? padded[0]?.id ?? "a");
    setExplanation(question.explanation ?? "");
    setMarks(question.marks);
    setQuestionDifficulty(question.difficulty);
    setAddOpen(true);
  };

  const openDetails = () => {
    setDetails({ title: data.title, description: data.description, duration_minutes: data.duration_minutes });
    setDetailsOpen(true);
  };

  const saveDetails = async () => {
    setBusy(true);
    try {
      await api(`/practice/tests/${data.id}`, { method: "PATCH", body: details });
      toast("Practice set updated.", "success");
      setDetailsOpen(false);
      test.reload();
    } catch (err) {
      toast([errorMessage(err), ...fieldErrors(err)].join(" "), "error");
    } finally {
      setBusy(false);
    }
  };

  const addQuestion = async () => {
    if (!topicId) return toast("Choose a topic for the question.", "error");
    const payload: PracticeQuestionCreatePayload = {
      topic_id: topicId,
      question_text: questionText,
      question_type: "SINGLE_CHOICE",
      options: options.filter((option) => option.text.trim()).map((option) => ({ ...option, text: option.text.trim() })),
      correct_answers: [correct],
      explanation,
      marks,
      difficulty: questionDifficulty,
      bloom_level: "UNDERSTAND",
    };
    setBusy(true);
    try {
      if (editingId) {
        await api(`/practice/tests/${data.id}/questions/${editingId}`, { method: "PUT", body: payload });
        toast("Question updated.", "success");
      } else {
        await api(`/practice/tests/${data.id}/questions`, { method: "POST", body: [payload] });
        toast("Question added.", "success");
      }
      setAddOpen(false);
      setQuestionText("");
      setExplanation("");
      test.reload();
    } catch (err) {
      toast([errorMessage(err), ...fieldErrors(err)].join(" "), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <Link to="/teacher/practice" className="back-link"><ArrowLeft size={15} /> Practice studio</Link>
      <PageHeader
        eyebrow={`${data.subject_name} · ${data.difficulty.toLowerCase()}`}
        title={data.title}
        description={data.description || "No description provided."}
        actions={
          <div className="page-actions-inline">
            {editable && isDraft && (
              <Button variant="secondary" onClick={() => setStatus("PUBLISHED")} loading={busy} icon={<Send size={15} />}>
                Publish
              </Button>
            )}
            {editable && data.status === "PUBLISHED" && untouched && (
              <Button variant="ghost" onClick={() => setStatus("DRAFT")} loading={busy}>
                Back to draft
              </Button>
            )}
            {editable && data.status === "PUBLISHED" && (
              <Button variant="ghost" onClick={() => setStatus("ARCHIVED")} loading={busy}>
                Archive
              </Button>
            )}
            {editable && data.status === "ARCHIVED" && (
              <Button variant="secondary" onClick={() => setStatus("PUBLISHED")} loading={busy}>
                Republish
              </Button>
            )}
            {editable && data.status === "ARCHIVED" && untouched && (
              <Button variant="ghost" onClick={() => setStatus("DRAFT")} loading={busy}>
                Reopen draft
              </Button>
            )}
            {editable && (untouched || data.status !== "PUBLISHED") && (
              <Button variant="ghost" onClick={openDetails} icon={<Pencil size={15} />}>
                Edit details
              </Button>
            )}
            <Badge tone={statusTone(data.status)}>{titleCase(data.status)}</Badge>
          </div>
        }
      />

      {!editable && (
        <div className="notice">
          <Eye size={16} />
          <span>View only. {data.created_by_name} owns this practice set; only they or the HOD can change it.</span>
        </div>
      )}
      {editable && !isDraft && (
        <div className="notice">
          <span>
            Questions can be changed only while the set is a draft.
            {untouched ? " Move it back to draft to edit." : " Students have attempted it, so archive it and create a new set to change questions."}
          </span>
        </div>
      )}

      <div className="stat-grid stat-grid-tight">
        <Stat label="Questions" value={data.question_count} icon={<BookOpenCheck size={17} />} />
        <Stat label="Total marks" value={data.total_marks} icon={<Target size={17} />} />
        <Stat label="Duration" value={`${data.duration_minutes} min`} icon={<Clock3 size={17} />} />
        <Stat label="Attempts" value={data.attempt_count} hint={`${analytics.data?.student_rows.length ?? 0} students`} icon={<Users size={17} />} />
      </div>

      <div className="tabs">
        <button className={tab === "questions" ? "active" : ""} onClick={() => setTab("questions")}>
          Questions <span>{data.question_count}</span>
        </button>
        <button className={tab === "analytics" ? "active" : ""} onClick={() => setTab("analytics")}>
          Analytics <span>{analytics.data?.attempts ?? 0}</span>
        </button>
      </div>

      {tab === "questions" ? (
        <div className="stack">
          {editable && isDraft && (
            <Card className="inline-actions-card">
              <div>
                <strong>Build the question set</strong>
                <p>Generate grounded single-choice questions from the syllabus, or add them manually.</p>
              </div>
              <div className="button-row">
                <Button variant="secondary" icon={<Plus size={15} />} onClick={openAdd}>Add manually</Button>
                <Button icon={<Sparkles size={15} />} onClick={() => setGenerateOpen(true)}>Generate with AI</Button>
              </div>
            </Card>
          )}

          {data.questions.length ? (
            <div className="question-list">
              {data.questions.map((question, index) => (
                <Card className="review-item correct" key={question.id}>
                  <div className="review-head">
                    <span className="review-number">{index + 1}</span>
                    <div className="review-title">
                      <strong>{question.question_text}</strong>
                      <div className="review-tags">
                        <Badge tone="neutral">{question.topic_name}</Badge>
                        <Badge tone={difficultyTone(question.difficulty)}>{question.difficulty.toLowerCase()}</Badge>
                        <Badge tone="neutral">{titleCase(question.bloom_level)}</Badge>
                        <span className="muted">{question.marks} mark{question.marks === 1 ? "" : "s"}</span>
                      </div>
                    </div>
                    {editable && isDraft && (
                      <button
                        className="icon-btn"
                        aria-label={`Edit question ${index + 1}`}
                        disabled={busy}
                        onClick={() => openEdit(question)}
                      >
                        <Pencil size={15} />
                      </button>
                    )}
                    {editable && isDraft && (
                      <button
                        className="icon-btn danger"
                        aria-label={`Delete question ${index + 1}`}
                        disabled={busy}
                        onClick={() => deleteQuestion(question.id)}
                      >
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                  <div className="review-options compact-options">
                    {question.options.map((option) => {
                      const isCorrect = (question.correct_answers ?? []).includes(option.id);
                      return (
                        <div key={option.id} className={`review-option ${isCorrect ? "correct-answer" : ""}`}>
                          <span className="option-key">{option.id.toUpperCase()}</span>
                          <span>{option.text}</span>
                          {isCorrect && <CheckCircle2 size={15} />}
                        </div>
                      );
                    })}
                  </div>
                  {question.explanation && (
                    <div className="explanation"><strong>Explanation</strong><p>{question.explanation}</p></div>
                  )}
                </Card>
              ))}
            </div>
          ) : (
            <Card>
              <EmptyState
                icon={<Sparkles size={22} />}
                title="No questions yet"
                description="Generate a set from your uploaded syllabus or write the first question yourself."
                action={
                  editable && isDraft ? (
                    <Button onClick={() => setGenerateOpen(true)} icon={<Sparkles size={15} />}>Generate questions</Button>
                  ) : undefined
                }
              />
            </Card>
          )}
        </div>
      ) : (
        <div className="stack">
          {analytics.error ? (
            <ErrorState message={analytics.error} onRetry={analytics.reload} />
          ) : analytics.data && analytics.data.attempts > 0 ? (
            <>
              <div className="stat-grid stat-grid-tight">
                <Stat label="Attempts" value={analytics.data.attempts} icon={<Users size={17} />} />
                <Stat label="Average score" value={`${analytics.data.average_percentage.toFixed(1)}%`} tone="accent" />
                <Stat label="Average accuracy" value={`${analytics.data.average_accuracy.toFixed(1)}%`} />
                <Stat label="Completion" value={`${analytics.data.completion_rate.toFixed(0)}%`} tone="success" />
              </div>
              <div className="split-grid">
                <Card>
                  <div className="card-head"><div><div className="eyebrow">Cohort</div><h3>Topic accuracy</h3></div><BarChart3 size={17} className="muted" /></div>
                  <TopicBars
                    items={(analytics.data.topic_breakdown as Array<{ topic_name: string; attempted: number; correct: number; accuracy: number }>).map((row) => ({
                      label: row.topic_name,
                      percentage: row.accuracy,
                      detail: `${row.correct}/${row.attempted}`,
                    }))}
                  />
                </Card>
                <Card>
                  <div className="card-head"><div><div className="eyebrow">Roster</div><h3>Student outcomes</h3></div></div>
                  <div className="student-list">
                    {(analytics.data.student_rows as Array<{ student_id: string; student_name: string; student_email: string; best_percentage: number; last_attempt: string; attempts: number }>).map((row) => (
                      <div className="student-row" key={row.student_id}>
                        <div><strong>{row.student_name}</strong><span>{row.student_email} · {row.attempts} attempt{row.attempts === 1 ? "" : "s"}</span></div>
                        <Badge tone={scoreTone(row.best_percentage)}>{row.best_percentage.toFixed(1)}%</Badge>
                        <span className="muted">{formatDateTime(row.last_attempt)}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              </div>
            </>
          ) : (
            <Card><EmptyState icon={<BarChart3 size={22} />} title="No submissions yet" description="Publish the set and share it with students to unlock cohort analytics." /></Card>
          )}
        </div>
      )}

      <Modal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        title="Generate practice questions"
        description="Questions are grounded in uploaded syllabus material and stored as single-choice items for review."
        footer={
          <>
            <Button variant="ghost" onClick={() => setGenerateOpen(false)}>Cancel</Button>
            <Button loading={busy} onClick={generate} icon={<Sparkles size={15} />}>Generate</Button>
          </>
        }
      >
        <div className="form-grid">
          <Field label="Question count">
            <Input type="number" min={1} max={20} value={count} onChange={(event) => setCount(Number(event.target.value))} />
          </Field>
          <Field label="Difficulty">
            <Select value={genDifficulty} onChange={(event) => setGenDifficulty(event.target.value)}>
              <option value="EASY">Easy</option>
              <option value="MEDIUM">Medium</option>
              <option value="HARD">Hard</option>
            </Select>
          </Field>
          <Field label="Topic focus" className="span-2">
            <Select value={genTopic} onChange={(event) => setGenTopic(event.target.value)}>
              <option value="">All topics in this set</option>
              {topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
            </Select>
          </Field>
          <label className="checkbox-row span-2">
            <input type="checkbox" checked={replace} onChange={(event) => setReplace(event.target.checked)} />
            Replace existing questions (drafts only)
          </label>
        </div>
      </Modal>

      <Modal
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        title="Edit practice set"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDetailsOpen(false)}>Cancel</Button>
            <Button loading={busy} onClick={saveDetails}>Save</Button>
          </>
        }
      >
        <div className="form-grid">
          <Field label="Title" className="span-2">
            <Input value={details.title} onChange={(event) => setDetails({ ...details, title: event.target.value })} minLength={3} maxLength={200} />
          </Field>
          <Field label="Description" className="span-2">
            <Textarea value={details.description} onChange={(event) => setDetails({ ...details, description: event.target.value })} rows={3} />
          </Field>
          <Field label="Duration (minutes)">
            <Input type="number" min={1} max={240} value={details.duration_minutes} onChange={(event) => setDetails({ ...details, duration_minutes: Number(event.target.value) })} />
          </Field>
        </div>
      </Modal>

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title={editingId ? "Edit question" : "Add a question"}
        description="Single-choice questions only. Options and the correct answer appear to students after submission."
        width="640px"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button loading={busy} onClick={addQuestion} icon={editingId ? <Pencil size={15} /> : <Plus size={15} />}>
              {editingId ? "Save question" : "Add question"}
            </Button>
          </>
        }
      >
        <div className="form-grid">
          <Field label="Topic">
            <Select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
              <option value="">Select topic…</option>
              {topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
            </Select>
          </Field>
          <Field label="Marks">
            <Input type="number" min={1} max={10} value={marks} onChange={(event) => setMarks(Number(event.target.value))} />
          </Field>
          <Field label="Question" className="span-2">
            <Textarea value={questionText} onChange={(event) => setQuestionText(event.target.value)} rows={3} placeholder="Write the question stem…" />
          </Field>
          {options.map((option, index) => (
            <Field label={`Option ${String.fromCharCode(65 + index)}`} key={option.id}>
              <Input
                value={option.text}
                onChange={(event) =>
                  setOptions((current) => current.map((item) => item.id === option.id ? { ...item, text: event.target.value } : item))
                }
              />
            </Field>
          ))}
          <Field label="Correct option">
            <Select value={correct} onChange={(event) => setCorrect(event.target.value)}>
              {options.map((option, index) => <option key={option.id} value={option.id}>{String.fromCharCode(65 + index)}</option>)}
            </Select>
          </Field>
          <Field label="Difficulty">
            <Select value={questionDifficulty} onChange={(event) => setQuestionDifficulty(event.target.value)}>
              <option value="EASY">Easy</option>
              <option value="MEDIUM">Medium</option>
              <option value="HARD">Hard</option>
            </Select>
          </Field>
          <Field label="Explanation" className="span-2">
            <Textarea value={explanation} onChange={(event) => setExplanation(event.target.value)} rows={2} placeholder="Shown after submission." />
          </Field>
        </div>
      </Modal>

      <Card>
        <KeyValue
          items={[
            { label: "Set ID", value: <code>{data.id.slice(0, 8)}</code> },
            { label: "Topics", value: data.topics.join(", ") || "—" },
            { label: "Created", value: formatDateTime(data.last_attempt_at ?? new Date().toISOString()) },
          ]}
        />
      </Card>
    </div>
  );
}
