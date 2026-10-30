import { useEffect, useRef, useState } from "react";
import {
  BookMarked,
  FileText,
  Library,
  Search,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api, errorMessage } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import type { RagResult, Subject, SyllabusDocument } from "../../lib/types";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, PageLoading, Select } from "../../components/ui";
import { bytes, formatDateTime } from "../../lib/format";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";

export function Syllabus() {
  const { meta } = useAuth();
  const { toast } = useToast();
  const subjects = useApi<Subject[]>("/subjects");
  const [subjectId, setSubjectId] = useState("");
  const documents = useApi<SyllabusDocument[]>(subjectId ? `/syllabus/documents?subject_id=${subjectId}` : null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RagResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    // Open on a subject the caller can actually use, not merely the first alphabetically.
    const first = subjects.data?.find((item) => item.assigned_to_me);
    if (!subjectId && first) setSubjectId(first.id);
  }, [subjects.data, subjectId]);

  if (subjects.loading && !subjects.data) return <PageLoading label="Loading syllabus library" />;
  if (subjects.error) return <ErrorState message={subjects.error} onRetry={subjects.reload} />;

  const subject = (subjects.data ?? []).find((item) => item.id === subjectId);

  const upload = async (file: File) => {
    if (!subjectId) return;
    setUploading(true);
    const form = new FormData();
    form.append("subject_id", subjectId);
    form.append("file", file);
    try {
      await api("/syllabus/documents", { method: "POST", form });
      toast(`${file.name} ingested and indexed.`, "success");
      documents.reload();
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (document: SyllabusDocument) => {
    if (!window.confirm(`Delete ${document.original_name}? Its indexed chunks will be removed too.`)) return;
    try {
      await api(`/syllabus/documents/${document.id}`, { method: "DELETE" });
      toast("Document deleted.", "success");
      documents.reload();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  };

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!subjectId || query.trim().length < 2) return;
    setSearching(true);
    try {
      const data = await api<RagResult[]>(
        `/syllabus/search?subject_id=${subjectId}&q=${encodeURIComponent(query.trim())}&top_k=5`,
      );
      setResults(data);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Retrieval corpus"
        title="Syllabus library"
        description="Upload the source material that grounds question generation. Every chunk is embedded and retrievable."
        actions={
          <Select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); setResults(null); }} className="subject-select">
            {(subjects.data ?? []).filter((item) => item.assigned_to_me).map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}
          </Select>
        }
      />

      <div className="syllabus-grid">
        <div className="stack">
          <Card className="upload-card">
            <div className="upload-icon"><UploadCloud size={24} /></div>
            <div>
              <h3>Add syllabus material</h3>
              <p>
                PDF, DOCX or TXT up to {meta?.upload_limits.max_mb ?? 15} MB. Unit-wise notes produce the best generated questions.
              </p>
              <div className="upload-actions">
                <input
                  ref={fileInput}
                  type="file"
                  accept=".pdf,.docx,.txt"
                  className="hidden-input"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) upload(file);
                  }}
                />
                <Button loading={uploading} onClick={() => fileInput.current?.click()} icon={<UploadCloud size={16} />}>
                  Choose file &amp; ingest
                </Button>
                <span className="muted">{subject ? subject.name : "Select a subject"}</span>
              </div>
            </div>
          </Card>

          <Card>
            <div className="card-head">
              <div><div className="eyebrow">Indexed corpus</div><h3>Documents</h3></div>
              <Badge tone="neutral">{documents.data?.length ?? 0} files</Badge>
            </div>
            {documents.loading && !documents.data ? (
              <PageLoading label="Loading documents" />
            ) : documents.error ? (
              <ErrorState message={documents.error} onRetry={documents.reload} />
            ) : documents.data?.length ? (
              <div className="document-list">
                {documents.data.map((document) => (
                  <div className="document-row" key={document.id}>
                    <div className="document-icon"><FileText size={18} /></div>
                    <div className="document-main">
                      <strong>{document.original_name}</strong>
                      <span>
                        {bytes(document.size_bytes)} · {document.chunk_count} chunks · uploaded {formatDateTime(document.created_at)}
                      </span>
                    </div>
                    <Badge tone={document.status === "PROCESSED" ? "success" : "warning"}>{document.status}</Badge>
                    <button className="icon-btn danger" onClick={() => remove(document)} aria-label={`Delete ${document.original_name}`}>
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<Library size={22} />}
                title="No documents for this subject"
                description="Upload a syllabus or unit notes to enable grounded generation and retrieval search."
              />
            )}
          </Card>
        </div>

        <div className="stack">
          <Card className="search-card">
            <div className="card-head">
              <div><div className="eyebrow">Retrieval preview</div><h3>Search the corpus</h3></div>
              <Sparkles size={17} className="muted" />
            </div>
            <form className="search-wrap" onSubmit={search}>
              <Search size={16} />
              <input
                className="search-input"
                placeholder="e.g. lossless decomposition, deadlock conditions…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <Button type="submit" size="sm" loading={searching} disabled={query.trim().length < 2}>Search</Button>
            </form>
            <div className="search-results">
              {results === null ? (
                <p className="muted">Search previews exactly what the generator retrieves: ranked syllabus chunks with cosine scores.</p>
              ) : results.length ? (
                results.map((result) => (
                  <div className="search-hit" key={result.chunk_id}>
                    <div className="search-hit-head">
                      <strong>{result.document_name}</strong>
                      <Badge tone="accent">{result.score.toFixed(3)}</Badge>
                    </div>
                    <p>{result.content}</p>
                  </div>
                ))
              ) : (
                <EmptyState icon={<BookMarked size={20} />} title="No matching chunks" description="Try a different phrase or upload more material." />
              )}
            </div>
          </Card>

          <Card className="security-card">
            <div className="security-icon"><Sparkles size={19} /></div>
            <div>
              <strong>How grounding works</strong>
              <p>
                Documents are cleaned, chunked with overlap, embedded with the configured embedding provider, and retrieved per
                topic during generation. Candidate questions are rejected when they drift from the retrieved context.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
