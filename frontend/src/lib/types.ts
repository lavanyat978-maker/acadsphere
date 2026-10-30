export type Role = "STUDENT" | "TEACHER" | "HOD";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  department: string;
  mfa_enabled: boolean;
  must_change_password?: boolean;
  last_login_at?: string | null;
}

export interface AdminUser extends User {
  is_active: boolean;
  must_change_password: boolean;
  created_at?: string | null;
  subject_ids: string[];
}

export interface DemoAccount {
  email: string;
  role: Role;
  full_name: string;
  title: string;
  password: string;
  mfa: string;
}

export interface Meta {
  app_name: string;
  version: string;
  env: string;
  demo_mode: boolean;
  ai: {
    name: string;
    mode: string;
    model: string;
    demo_mode: boolean;
    embedding_provider: string;
    warning?: string;
  };
  encryption: {
    active_key_id: string;
    known_key_ids: string[];
    algorithm: string;
    rotation_supported: boolean;
  };
  upload_limits: { max_mb: number; extensions: string[] };
  release_window_minutes: number;
  print_limit: number;
  demo_accounts: DemoAccount[];
}

export interface Topic {
  id: string;
  name: string;
  unit: string;
  description?: string;
}

export interface Subject {
  id: string;
  code: string;
  name: string;
  description: string;
  department: string;
  topics: Topic[];
  assigned_teachers: string[];
  teacher_ids: string[];
  student_count: number;
  assigned_to_me: boolean;
}

export interface EnrolledStudent {
  id: string;
  email: string;
  full_name: string;
  department: string;
  is_active: boolean;
  enrolled_at: string;
}

export interface DemoCodeResponse {
  code: string;
  seconds_remaining: number;
  warning: string;
}

export interface LoginResponse {
  mfa_required?: boolean;
  mfa_token?: string;
  expires_at?: string;
  method?: string;
  access_token?: string;
  user?: User;
  enrollment_required?: boolean;
  secret?: string | null;
  otpauth_uri?: string | null;
}

export interface PracticeTest {
  id: string;
  subject_id: string;
  subject_name: string;
  title: string;
  description: string;
  duration_minutes: number;
  difficulty: string;
  question_count: number;
  total_marks: number;
  status: string;
  topics: string[];
  attempt_count: number;
  best_score: number | null;
  last_attempt_at: string | null;
  can_edit: boolean;
  created_by_name: string;
  opens_at: string | null;
  closes_at: string | null;
  max_attempts: number | null;
  availability: "OPEN" | "UPCOMING" | "CLOSED";
  attempts_remaining: number | null;
}

export interface QuestionOption {
  id: string;
  text: string;
}

export interface Question {
  id: string;
  question_text: string;
  question_type: string;
  options: QuestionOption[];
  marks: number;
  difficulty: string;
  bloom_level: string;
  topic_id: string;
  topic_name: string;
  position: number;
  explanation?: string | null;
  correct_answers?: string[] | null;
}

export interface PracticeQuestionCreatePayload {
  topic_id: string;
  question_text: string;
  question_type: string;
  options: QuestionOption[];
  correct_answers: string[];
  explanation: string;
  marks: number;
  difficulty: string;
  bloom_level: string;
}

export interface PracticeTestDetail extends PracticeTest {
  questions: Question[];
  _meta?: {
    generated: number;
    mode: string;
    provider: string;
    warning: string;
  };
}

export interface Attempt {
  id: string;
  test_id: string;
  test_title: string;
  duration_minutes: number;
  status: string;
  started_at: string;
  expires_at: string;
  server_time: string;
  total_marks: number;
  warning: string;
  saved_answers: Record<string, { selected: string[]; response_time_seconds: number }>;
  last_saved_at: string | null;
  questions: Question[];
}

export interface ReviewItem {
  question_id: string;
  question_text: string;
  question_type: string;
  options: QuestionOption[];
  selected: string[];
  correct_answers: string[];
  is_correct: boolean;
  awarded_marks: number;
  marks: number;
  explanation: string;
  topic_name: string;
  difficulty: string;
  response_time_seconds: number;
}

export interface PracticeResult {
  id: string;
  attempt_id: string;
  test_id: string;
  test_title: string;
  subject_name: string;
  score: number;
  max_score: number;
  percentage: number;
  accuracy: number;
  avg_response_time: number;
  created_at: string;
  topic_breakdown: Array<Record<string, unknown>>;
  difficulty_breakdown: Array<Record<string, unknown>>;
  review: ReviewItem[];
  auto_submitted?: boolean;
  student_name?: string | null;
}

export interface TopicPerformance {
  topic_id: string;
  topic_name: string;
  subject_name: string;
  attempted: number;
  correct: number;
  accuracy: number;
  avg_time: number;
  strength: string;
}

export interface StudentAnalytics {
  total_attempts: number;
  average_percentage: number;
  best_percentage: number;
  overall_accuracy: number;
  avg_response_time: number;
  improvement: number;
  weak_topics: TopicPerformance[];
  topic_performance: TopicPerformance[];
  difficulty_performance: Array<Record<string, unknown>>;
  trend: Array<Record<string, unknown>>;
  recommendations: Array<Record<string, unknown>>;
}

export interface HistoryItem {
  attempt_id: string;
  test_id: string;
  test_title: string;
  subject_name: string;
  started_at: string;
  submitted_at: string | null;
  score: number;
  max_score: number;
  percentage: number;
  accuracy: number;
  status: string;
}

export type ExamSource = "GENERATED" | "UPLOADED";

export interface Exam {
  id: string;
  source: ExamSource;
  can_edit: boolean;
  created_by_id: string;
  paper_version: number | null;
  paper_file_name: string;
  paper_page_count: number;
  replaceable_until: string | null;
  window_opens_at: string | null;
  window_closes_at: string | null;
  subject_id: string;
  subject_name: string;
  title: string;
  exam_type: string;
  exam_date: string;
  duration_minutes: number;
  total_marks: number;
  venue: string;
  instructions: string;
  status: string;
  created_by_name: string;
  topics: Array<Record<string, unknown>>;
  configuration: {
    total_marks: number;
    pattern: Array<{ marks: number; count: number; question_type: string; label: string }>;
    difficulty_distribution: Record<string, number>;
    bloom_distribution: Record<string, number>;
    topic_distribution: Record<string, number>;
    instructions: string;
    retry_limit: number;
    frozen: boolean;
    frozen_at: string | null;
    frozen_hash: string;
  } | null;
  generation_status: string | null;
  generation_request_id: string | null;
  paper_state: string | null;
  paper_id: string | null;
  created_at: string;
}

export interface GenerationStatus {
  request_id: string;
  exam_id: string;
  status: string;
  mode: string;
  provider: string;
  attempts: number;
  candidates_generated: number;
  candidates_rejected: number;
  error: string | null;
  stage: string;
  progress: number;
  paper_state: string | null;
  validation: Record<string, { passed: number; failed: number }>;
  started_at: string | null;
  completed_at: string | null;
  secure_summary: string;
}

export interface SecurePaper {
  paper_id: string;
  exam_id: string;
  version: number;
  state: string;
  total_marks: number;
  question_count: number;
  algorithm: string;
  key_id: string;
  ciphertext_sha256: string;
  ciphertext_bytes: number;
  content_type: string;
  original_name: string;
  page_count: number;
  encrypted_at: string | null;
  locked_at: string | null;
  plaintext_hash_matches: boolean;
  transitions: Array<{
    from: string;
    to: string;
    at: string;
    actor_role: string;
    reason: string;
  }>;
}

export interface ReleaseQueueItem {
  paper_id: string;
  exam_id: string;
  source: ExamSource;
  paper_version: number;
  page_count: number;
  prints_used: number;
  prints_remaining: number;
  exam_title: string;
  subject_name: string;
  exam_type: string;
  exam_date: string;
  duration_minutes: number;
  total_marks: number;
  venue: string;
  paper_state: string;
  encrypted_at: string | null;
  window_opens_at: string;
  window_closes_at: string;
  window_open: boolean;
  release: {
    id: string;
    status: string;
    authorized_at: string;
    token_expires_at: string;
    print_count: number;
    reason: string;
    authorized_by_name: string;
    decrypted_at: string | null;
    printed_at: string | null;
  } | null;
}

export interface AuditLog {
  seq: number;
  event: string;
  actor_email: string;
  actor_role: string;
  resource_type: string;
  resource_id: string;
  success: boolean;
  ip_address: string;
  created_at: string;
  metadata_json: Record<string, unknown>;
  record_hash: string;
}

export interface AuditPage {
  total: number;
  limit: number;
  offset: number;
  items: AuditLog[];
}

export interface SecurityEvent {
  id: string;
  event_type: string;
  severity: string;
  summary: string;
  actor_id: string | null;
  ip_address: string;
  detail: Record<string, unknown>;
  created_at: string;
  resolved?: boolean;
  resolved_at?: string | null;
}

export interface SyllabusDocument {
  id: string;
  subject_id: string;
  original_name: string;
  content_type: string;
  size_bytes: number;
  chunk_count: number;
  char_count: number;
  status: string;
  created_at: string;
  uploaded_by_name: string;
}

export interface RagResult {
  chunk_id: string;
  document_id: string;
  document_name: string;
  content: string;
  score: number;
}

export interface TeacherDashboard {
  subjects: number;
  practice_tests: number;
  published_tests: number;
  total_attempts: number;
  average_percentage: number;
  active_students: number;
  secured_papers: number;
  recent_exams: Array<{
    id: string;
    title: string;
    exam_type: string;
    exam_date: string;
    status: string;
    total_marks: number;
  }>;
}

export interface HodDashboard {
  upcoming_exams: number;
  secured_papers: number;
  released_papers: number;
  pending_release: number;
  security_events: SecurityEvent[];
  recent_releases: Array<{
    id: string;
    paper_id: string;
    status: string;
    authorized_at: string;
    print_count: number;
  }>;
  next_exams: Array<{
    id: string;
    title: string;
    exam_type: string;
    exam_date: string;
    status: string;
    total_marks: number;
  }>;
  department: DepartmentOverview;
}

export interface DepartmentOverview {
  students: number;
  practice_attempts: number;
  average_percentage: number;
  subjects: Array<{
    subject_id: string;
    code: string;
    name: string;
    teachers: number;
    students: number;
    published_tests: number;
    attempts: number;
    active_students: number;
    average_percentage: number;
    exams: number;
  }>;
  teachers: Array<{
    teacher_id: string;
    name: string;
    subjects: number;
    practice_tests: number;
    published_tests: number;
    attempts: number;
    average_percentage: number;
    exams: number;
  }>;
}

export interface ReleaseHistoryItem {
  release_id: string;
  paper_id: string;
  exam_title: string;
  subject_name: string;
  status: string;
  authorized_by_name: string;
  authorized_at: string;
  decrypted_at: string | null;
  printed_at: string | null;
  print_count: number;
  reason: string;
}

export interface StudentDashboard {
  enrolled_subjects: number;
  tests_available: number;
  tests_attempted: number;
  summary: {
    total_attempts: number;
    average_percentage: number;
    best_percentage: number;
    overall_accuracy: number;
    improvement: number;
  };
  weak_topics: TopicPerformance[];
  recent_attempts: HistoryItem[];
  trend: Array<Record<string, unknown>>;
  recommendations: Array<Record<string, unknown>>;
}

export interface PracticeAnalytics {
  test_id: string;
  test_title: string;
  attempts: number;
  average_percentage: number;
  average_accuracy: number;
  completion_rate: number;
  topic_breakdown: Array<Record<string, unknown>>;
  student_rows: Array<Record<string, unknown>>;
}
