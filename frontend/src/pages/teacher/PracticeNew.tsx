import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, BookOpenCheck, CheckCircle2, ClipboardList, Sparkles } from "lucide-react";
import { api, errorMessage, fieldErrors } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { fromLocalInputValue } from "../../lib/format";
import type { PracticeTestDetail, Subject } from "../../lib/types";
import { Button, Card, ErrorState, Field, Input, PageHeader, PageLoading, Select, Textarea } from "../../components/ui";
import { useToast } from "../../context/ToastContext";

export function PracticeNew() {
  const subjects = useApi<Subject[]>("/subjects");
  const navigate = useNavigate();
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [duration, setDuration] = useState(20);
  const [opensAt, setOpensAt] = useState("");
  const [closesAt, setClosesAt] = useState("");
  const [maxAttempts, setMaxAttempts] = useState("");
  const [difficulty, setDifficulty] = useState("MEDIUM");

  const subject = useMemo(
    () => (subjects.data ?? []).find((item) => item.id === subjectId && item.assigned_to_me),
    [subjects.data, subjectId],
  );

  if (subjects.loading && !subjects.data) return <PageLoading label="Loading your subjects" />;
  if (subjects.error) return <ErrorState message={subjects.error} onRetry={subjects.reload} />;

  const toggleTopic = (topicId: string) => {
    setSelectedTopics((current) =>
      current.includes(topicId) ? current.filter((id) => id !== topicId) : [...current, topicId],
    );
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!subjectId) return setError("Select a subject.");
    if (!selectedTopics.length) return setError("Select at least one topic.");
    setBusy(true);
    try {
      const test = await api<PracticeTestDetail>("/practice/tests", {
        method: "POST",
        body: {
          subject_id: subjectId,
          title,
          description,
          duration_minutes: duration,
          opens_at: fromLocalInputValue(opensAt),
          closes_at: fromLocalInputValue(closesAt),
          max_attempts: maxAttempts ? Number(maxAttempts) : null,
          difficulty,
          topic_ids: selectedTopics,
          questions: [],
        },
      });
      toast("Draft created. Add or generate questions next.", "success");
      navigate(`/teacher/practice/${test.id}`);
    } catch (err) {
      setError([errorMessage(err), ...fieldErrors(err)].join(" "));
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <Link to="/teacher/practice" className="back-link"><ArrowLeft size={15} /> Practice studio</Link>
      <PageHeader
        eyebrow="New practice set"
        title="Create a draft"
        description="Pick the topic scope first. Questions can be written manually or generated from syllabus material."
      />

      <form onSubmit={submit} className="form-layout">
        <div className="stack">
          <Card className="form-card">
            <div className="form-section-head">
              <ClipboardList size={18} />
              <div><h3>Set details</h3><p>What students see in the practice library.</p></div>
            </div>
            <div className="form-grid">
              <Field label="Subject">
                <Select
                  value={subjectId}
                  onChange={(event) => {
                    setSubjectId(event.target.value);
                    setSelectedTopics([]);
                  }}
                  required
                >
                  <option value="">Select a subject…</option>
                  {(subjects.data ?? []).filter((item) => item.assigned_to_me).map((item) => (
                    <option key={item.id} value={item.id}>{item.code} · {item.name}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Title">
                <Input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="e.g. Normalization checkpoint"
                  minLength={3}
                  maxLength={200}
                  required
                />
              </Field>
              <Field label="Description" className="span-2">
                <Textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="What this set covers and when to attempt it."
                  rows={3}
                  maxLength={2000}
                />
              </Field>
              <Field label="Duration (minutes)">
                <Input
                  type="number"
                  min={1}
                  max={240}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                />
              </Field>
              <Field label="Baseline difficulty">
                <Select value={difficulty} onChange={(event) => setDifficulty(event.target.value)}>
                  <option value="EASY">Easy</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HARD">Hard</option>
                </Select>
              </Field>
              <Field label="Opens at (optional)" hint="Students can see it before then but cannot start.">
                <Input type="datetime-local" value={opensAt} onChange={(event) => setOpensAt(event.target.value)} />
              </Field>
              <Field label="Closes at (optional)" hint="Attempts in progress end at this time.">
                <Input type="datetime-local" value={closesAt} onChange={(event) => setClosesAt(event.target.value)} />
              </Field>
              <Field label="Attempts per student (optional)" hint="Leave empty for unlimited practice.">
                <Input type="number" min={1} max={50} value={maxAttempts} onChange={(event) => setMaxAttempts(event.target.value)} />
              </Field>
            </div>
          </Card>

          <Card className="form-card">
            <div className="form-section-head">
              <BookOpenCheck size={18} />
              <div><h3>Topic scope</h3><p>Analytics and recommendations are grouped by these topics.</p></div>
            </div>
            {!subject ? (
              <p className="muted">Select a subject to load its topics.</p>
            ) : (
              <div className="topic-select-grid">
                {subject.topics.map((topic) => {
                  const selected = selectedTopics.includes(topic.id);
                  return (
                    <button
                      type="button"
                      key={topic.id}
                      className={`topic-option ${selected ? "selected" : ""}`}
                      onClick={() => toggleTopic(topic.id)}
                    >
                      <span className="topic-check">{selected && <CheckCircle2 size={15} />}</span>
                      <span><strong>{topic.name}</strong><em>{topic.unit}</em></span>
                    </button>
                  );
                })}
              </div>
            )}
            {subject && !subject.topics.length && (
              <p className="muted">No topics exist for this subject yet. Ask the HOD to add topics.</p>
            )}
          </Card>

          {error && <div className="form-error">{error}</div>}

          <div className="form-actions">
            <Link to="/teacher/practice"><Button type="button" variant="ghost">Cancel</Button></Link>
            <Button type="submit" loading={busy} icon={<Sparkles size={16} />}>
              Create draft
            </Button>
          </div>
        </div>

        <aside className="stack">
          <Card className="side-summary">
            <div className="eyebrow">Draft summary</div>
            <h3>{title || "Untitled practice set"}</h3>
            <p>{subject ? `${subject.code} · ${subject.name}` : "No subject selected"}</p>
            <div className="summary-rows">
              <div><span>Topics</span><strong>{selectedTopics.length}</strong></div>
              <div><span>Duration</span><strong>{duration} min</strong></div>
              <div><span>Difficulty</span><strong>{difficulty.toLowerCase()}</strong></div>
            </div>
            <div className="side-note">
              <Sparkles size={15} />
              <span>After creating the draft, request AI generation or add questions manually. Nothing is visible to students until you publish.</span>
            </div>
          </Card>
        </aside>
      </form>
    </div>
  );
}
