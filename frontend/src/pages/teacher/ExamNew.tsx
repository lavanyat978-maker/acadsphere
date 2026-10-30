import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  FileLock2,
  Gauge,
  Plus,
  Scale,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { api, errorMessage, fieldErrors } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { Exam, ExamSource, Subject } from "../../lib/types";
import { Badge, Button, Card, ErrorState, Field, Input, PageHeader, PageLoading, Select, Textarea } from "../../components/ui";
import { useToast } from "../../context/ToastContext";

interface PatternRow {
  marks: number;
  count: number;
  question_type: string;
  label: string;
}

const BLOOM_LEVELS = ["REMEMBER", "UNDERSTAND", "APPLY", "ANALYZE", "EVALUATE", "CREATE"];

function localInputValue(offsetDays: number) {
  const date = new Date(Date.now() + offsetDays * 86400000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export function ExamNew() {
  const subjects = useApi<Subject[]>("/subjects");
  const navigate = useNavigate();
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [source, setSource] = useState<ExamSource>("GENERATED");
  const [uploadMarks, setUploadMarks] = useState(50);
  const [subjectId, setSubjectId] = useState("");
  const [title, setTitle] = useState("");
  const [examType, setExamType] = useState("Internal Assessment 1");
  const [examDate, setExamDate] = useState(localInputValue(7));
  const [duration, setDuration] = useState(90);
  const [venue, setVenue] = useState("Examination Hall A");
  const [instructions, setInstructions] = useState("Answer all questions. Show all working.");
  const [retryLimit, setRetryLimit] = useState(3);
  const [pattern, setPattern] = useState<PatternRow[]>([
    { marks: 2, count: 5, question_type: "SHORT_ANSWER", label: "Section A" },
    { marks: 10, count: 2, question_type: "LONG_ANSWER", label: "Section B" },
  ]);
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [difficulty, setDifficulty] = useState<Record<string, number>>({ EASY: 30, MEDIUM: 50, HARD: 20 });
  const [bloom, setBloom] = useState<Record<string, number>>({
    REMEMBER: 20,
    UNDERSTAND: 30,
    APPLY: 30,
    ANALYZE: 20,
    EVALUATE: 0,
    CREATE: 0,
  });

  // Only subjects the caller works in (every subject for the HOD).
  const mySubjects = useMemo(() => (subjects.data ?? []).filter((item) => item.assigned_to_me), [subjects.data]);
  const subject = useMemo(() => mySubjects.find((item) => item.id === subjectId), [mySubjects, subjectId]);
  const uploaded = source === "UPLOADED";
  const totalMarks = pattern.reduce((sum, row) => sum + row.marks * row.count, 0);
  const questionCount = pattern.reduce((sum, row) => sum + row.count, 0);
  const topicSum = selectedTopics.reduce((sum, id) => sum + (weights[id] ?? 0), 0);
  const difficultySum = Object.values(difficulty).reduce((sum, value) => sum + value, 0);
  const bloomSum = Object.values(bloom).reduce((sum, value) => sum + value, 0);

  if (subjects.loading && !subjects.data) return <PageLoading label="Loading subjects" />;
  if (subjects.error) return <ErrorState message={subjects.error} onRetry={subjects.reload} />;

  const toggleTopic = (topicId: string) => {
    setSelectedTopics((current) => {
      if (current.includes(topicId)) {
        return current.filter((id) => id !== topicId);
      }
      const next = [...current, topicId];
      const share = Math.floor((100 / next.length) * 100) / 100;
      const rebuilt: Record<string, number> = {};
      next.forEach((id, index) => {
        rebuilt[id] = index === next.length - 1 ? Number((100 - share * (next.length - 1)).toFixed(2)) : share;
      });
      setWeights(rebuilt);
      return next;
    });
  };

  const balanceTopics = () => {
    if (!selectedTopics.length) return;
    const share = Math.floor((100 / selectedTopics.length) * 100) / 100;
    const next: Record<string, number> = {};
    selectedTopics.forEach((id, index) => {
      next[id] = index === selectedTopics.length - 1
        ? Number((100 - share * (selectedTopics.length - 1)).toFixed(2))
        : share;
    });
    setWeights(next);
  };

  const updatePattern = (index: number, patch: Partial<PatternRow>) => {
    setPattern((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  };

  const validate = (): string | null => {
    if (!subjectId) return "Select a subject.";
    if (title.trim().length < 3) return "Provide an examination title.";
    if (!examDate) return "Choose the examination date and time.";
    if (uploaded) {
      if (uploadMarks < 5 || uploadMarks > 200) return "Total marks must be between 5 and 200.";
      if (new Date(examDate).getTime() - Date.now() < 24 * 3600 * 1000) {
        return "Uploaded papers must be in place before the release window opens, 24 hours before the exam. Pick a later date.";
      }
      return null;
    }
    if (pattern.length === 0) return "Add at least one question group.";
    if (questionCount > 30) return "A paper may not exceed 30 questions.";
    if (selectedTopics.length === 0) return "Select at least one topic.";
    if (Math.abs(topicSum - 100) > 0.5) return `Topic weights must total 100 (currently ${topicSum.toFixed(1)}).`;
    if (Math.abs(difficultySum - 100) > 0.5) return `Difficulty weights must total 100 (currently ${difficultySum}).`;
    if (Math.abs(bloomSum - 100) > 0.5) return `Bloom weights must total 100 (currently ${bloomSum}).`;
    for (const row of pattern) {
      if (row.marks <= 0 || row.count <= 0) return "Marks and counts must be positive.";
    }
    return null;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const details = {
        source,
        subject_id: subjectId,
        title: title.trim(),
        exam_type: examType,
        exam_date: new Date(examDate).toISOString(),
        duration_minutes: duration,
        venue,
        instructions,
      };
      const exam = await api<Exam>("/exams", {
        method: "POST",
        body: uploaded ? { ...details, total_marks: uploadMarks } : {
          source,
          subject_id: subjectId,
          title: title.trim(),
          exam_type: examType,
          exam_date: new Date(examDate).toISOString(),
          duration_minutes: duration,
          venue,
          instructions,
          total_marks: totalMarks,
          pattern,
          topics: selectedTopics.map((topicId) => ({ topic_id: topicId, weight_percent: weights[topicId] ?? 0 })),
          difficulty_distribution: difficulty,
          bloom_distribution: bloom,
          retry_limit: retryLimit,
        },
      });
      toast(uploaded ? "Examination created. Upload the question paper next." : "Examination configuration saved as draft.", "success");
      navigate(`/teacher/exams/${exam.id}`);
    } catch (err) {
      setError([errorMessage(err), ...fieldErrors(err)].join(" "));
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <Link to="/teacher/exams" className="back-link"><ArrowLeft size={15} /> Examinations</Link>
      <PageHeader
        eyebrow="New examination"
        title={uploaded ? "Set up an uploaded-paper exam" : "Configure the blueprint"}
        description={
          uploaded
            ? "You will upload a finished PDF on the next screen. It is encrypted on receipt and only the HOD can open it, inside the release window."
            : "Everything here is frozen when you submit. Generation cannot change marks, topics or distributions."
        }
      />

      <form onSubmit={submit} className="form-layout">
        <div className="stack">
          <Card className="form-card">
            <div className="form-section-head">
              <FileLock2 size={18} />
              <div><h3>How is the paper produced?</h3><p>This choice is fixed once the examination is created.</p></div>
            </div>
            <div className="source-choice">
              <button type="button" className={`source-option ${!uploaded ? "active" : ""}`} onClick={() => setSource("GENERATED")}>
                <Sparkles size={20} />
                <div>
                  <strong>Generate with AI</strong>
                  <span>Questions are generated from the syllabus library, validated in five levels and sealed. Nobody, including you, reads the paper before release.</span>
                </div>
              </button>
              <button type="button" className={`source-option ${uploaded ? "active" : ""}`} onClick={() => setSource("UPLOADED")}>
                <Upload size={20} />
                <div>
                  <strong>Upload my paper</strong>
                  <span>Upload a finished PDF. It is encrypted immediately and printed by the HOD under the same controls. You can replace it until 24 hours before the exam.</span>
                </div>
              </button>
            </div>
          </Card>

          <Card className="form-card">
            <div className="form-section-head">
              <CalendarClock size={18} />
              <div><h3>Examination details</h3><p>Administrative information printed on the paper.</p></div>
            </div>
            <div className="form-grid">
              <Field label="Subject">
                <Select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); setSelectedTopics([]); setWeights({}); }} required>
                  <option value="">Select subject…</option>
                  {mySubjects.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}
                </Select>
              </Field>
              <Field label="Examination title">
                <Input value={title} onChange={(event) => setTitle(event.target.value)} minLength={3} maxLength={200} placeholder="e.g. DBMS Internal Assessment 1" required />
              </Field>
              <Field label="Assessment type">
                <Select value={examType} onChange={(event) => setExamType(event.target.value)}>
                  <option>Internal Assessment 1</option>
                  <option>Internal Assessment 2</option>
                  <option>Model Examination</option>
                  <option>Practical Viva</option>
                </Select>
              </Field>
              <Field label="Date & time">
                <Input type="datetime-local" value={examDate} onChange={(event) => setExamDate(event.target.value)} required />
              </Field>
              <Field label="Duration (minutes)">
                <Input type="number" min={15} max={300} value={duration} onChange={(event) => setDuration(Number(event.target.value))} />
              </Field>
              <Field label="Venue">
                <Input value={venue} onChange={(event) => setVenue(event.target.value)} maxLength={160} />
              </Field>
              {uploaded && (
                <Field label="Total marks">
                  <Input type="number" min={5} max={200} value={uploadMarks} onChange={(event) => setUploadMarks(Number(event.target.value))} />
                </Field>
              )}
              <Field label="Instructions" className="span-2">
                <Textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={2} maxLength={3000} />
              </Field>
            </div>
            {mySubjects.length === 0 && (
              <p className="field-hint warning-text">You are not assigned to any subject yet. Ask the HOD to assign you.</p>
            )}
          </Card>

          {!uploaded && (<>
          <Card className="form-card">
            <div className="form-section-head">
              <FileLock2 size={18} />
              <div><h3>Question pattern</h3><p>Marks × count defines the frozen paper total.</p></div>
              <Badge tone="accent">{totalMarks} marks · {questionCount} questions</Badge>
            </div>
            <div className="pattern-list">
              {pattern.map((row, index) => (
                <div className="pattern-row" key={index}>
                  <Field label="Marks each">
                    <Input type="number" min={1} max={50} value={row.marks} onChange={(event) => updatePattern(index, { marks: Number(event.target.value) })} />
                  </Field>
                  <Field label="Count">
                    <Input type="number" min={1} max={30} value={row.count} onChange={(event) => updatePattern(index, { count: Number(event.target.value) })} />
                  </Field>
                  <Field label="Question type">
                    <Select value={row.question_type} onChange={(event) => updatePattern(index, { question_type: event.target.value })}>
                      <option value="SHORT_ANSWER">Short answer</option>
                      <option value="LONG_ANSWER">Long answer</option>
                    </Select>
                  </Field>
                  <Field label="Section label">
                    <Input value={row.label} onChange={(event) => updatePattern(index, { label: event.target.value })} maxLength={80} />
                  </Field>
                  <button type="button" className="icon-btn danger" onClick={() => setPattern((current) => current.filter((_, i) => i !== index))} aria-label="Remove group">
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
            <Button type="button" variant="soft" size="sm" icon={<Plus size={15} />} onClick={() => setPattern((current) => [...current, { marks: 5, count: 1, question_type: "SHORT_ANSWER", label: `Section ${String.fromCharCode(65 + current.length)}` }])}>
              Add question group
            </Button>
            {questionCount < selectedTopics.length && selectedTopics.length > 0 && (
              <p className="field-hint warning-text">Provide at least one question slot per selected topic.</p>
            )}
          </Card>

          <Card className="form-card">
            <div className="form-section-head">
              <Scale size={18} />
              <div><h3>Topic weights</h3><p>Question counts are distributed across topics within a 10% tolerance.</p></div>
              <Badge tone={Math.abs(topicSum - 100) < 0.5 ? "success" : "danger"}>{topicSum.toFixed(1)}%</Badge>
            </div>
            {!subject ? (
              <p className="muted">Choose a subject to load topics.</p>
            ) : (
              <>
                <div className="topic-select-grid">
                  {subject.topics.map((topic) => {
                    const selected = selectedTopics.includes(topic.id);
                    return (
                      <button type="button" key={topic.id} className={`topic-option ${selected ? "selected" : ""}`} onClick={() => toggleTopic(topic.id)}>
                        <span className="topic-check">{selected && <CheckCircle2 size={15} />}</span>
                        <span><strong>{topic.name}</strong><em>{topic.unit}</em></span>
                      </button>
                    );
                  })}
                </div>
                {selectedTopics.length > 0 && (
                  <>
                    <Button type="button" variant="ghost" size="sm" onClick={balanceTopics}>Distribute evenly</Button>
                    <div className="weight-grid">
                      {selectedTopics.map((topicId) => {
                        const topic = subject.topics.find((item) => item.id === topicId);
                        return (
                          <Field label={topic?.name ?? "Topic"} key={topicId}>
                            <Input
                              type="number"
                              min={0}
                              max={100}
                              step={0.1}
                              value={weights[topicId] ?? 0}
                              onChange={(event) => setWeights((current) => ({ ...current, [topicId]: Number(event.target.value) }))}
                            />
                          </Field>
                        );
                      })}
                    </div>
                  </>
                )}
              </>
            )}
          </Card>

          <div className="split-grid">
            <Card className="form-card">
              <div className="form-section-head">
                <Gauge size={18} />
                <div><h3>Difficulty</h3><p>Target share of questions.</p></div>
                <Badge tone={difficultySum === 100 ? "success" : "danger"}>{difficultySum}%</Badge>
              </div>
              <div className="distribution-grid">
                {(["EASY", "MEDIUM", "HARD"] as const).map((level) => (
                  <Field label={level.toLowerCase()} key={level}>
                    <Input type="number" min={0} max={100} value={difficulty[level]} onChange={(event) => setDifficulty((current) => ({ ...current, [level]: Number(event.target.value) }))} />
                  </Field>
                ))}
              </div>
            </Card>
            <Card className="form-card">
              <div className="form-section-head">
                <Gauge size={18} />
                <div><h3>Bloom taxonomy</h3><p>Required cognitive spread.</p></div>
                <Badge tone={bloomSum === 100 ? "success" : "danger"}>{bloomSum}%</Badge>
              </div>
              <div className="distribution-grid distribution-grid-3">
                {BLOOM_LEVELS.map((level) => (
                  <Field label={level.toLowerCase()} key={level}>
                    <Input type="number" min={0} max={100} value={bloom[level]} onChange={(event) => setBloom((current) => ({ ...current, [level]: Number(event.target.value) }))} />
                  </Field>
                ))}
              </div>
            </Card>
          </div>

          <Card className="inline-actions-card">
            <div>
              <strong>Retry limit per slot</strong>
              <p>How many candidate attempts before a slot is declared exhausted (1–6).</p>
            </div>
            <Input type="number" min={1} max={6} value={retryLimit} onChange={(event) => setRetryLimit(Number(event.target.value))} className="narrow-input" />
          </Card>
          </>)}

          {error && <div className="form-error">{error}</div>}

          <div className="form-actions">
            <Link to="/teacher/exams"><Button type="button" variant="ghost">Cancel</Button></Link>
            <Button type="submit" loading={busy} icon={<FileLock2 size={16} />}>
              {uploaded ? "Create and continue to upload" : "Save draft configuration"}
            </Button>
          </div>
        </div>

        <aside className="stack">
          <Card className="side-summary">
            <div className="eyebrow">Blueprint summary</div>
            <h3>{title || "Untitled examination"}</h3>
            <p>{subject ? `${subject.code} · ${subject.name}` : "No subject selected"}</p>
            <div className="summary-rows">
              <div><span>Paper</span><strong>{uploaded ? "Uploaded PDF" : "AI generated"}</strong></div>
              <div><span>Total marks</span><strong>{uploaded ? uploadMarks : totalMarks}</strong></div>
              {!uploaded && <div><span>Questions</span><strong>{questionCount}</strong></div>}
              {!uploaded && <div><span>Topics</span><strong>{selectedTopics.length}</strong></div>}
              <div><span>Duration</span><strong>{duration} min</strong></div>
            </div>
            <div className="side-note">
              <FileLock2 size={15} />
              <span>
                {uploaded
                  ? "The PDF is encrypted with AES-256-GCM on upload. Other teachers cannot open it; the HOD prints at most three stamped copies."
                  : "Submitting freezes this blueprint with a SHA-256 hash. Any later change requires a new examination."}
              </span>
            </div>
          </Card>
        </aside>
      </form>
    </div>
  );
}
