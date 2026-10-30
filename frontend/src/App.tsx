import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { AppShell } from "./components/AppShell";
import { Login } from "./pages/Login";
import { StudentDashboard } from "./pages/student/Dashboard";
import { PracticeList } from "./pages/student/PracticeList";
import { PracticeDetail } from "./pages/student/PracticeDetail";
import { AttemptPage } from "./pages/student/Attempt";
import { Result } from "./pages/student/Result";
import { Analytics } from "./pages/student/Analytics";
import { History } from "./pages/student/History";
import { TeacherDashboard } from "./pages/teacher/Dashboard";
import { PracticeStudio } from "./pages/teacher/PracticeStudio";
import { PracticeNew } from "./pages/teacher/PracticeNew";
import { PracticeDetail as TeacherPracticeDetail } from "./pages/teacher/PracticeDetail";
import { Exams } from "./pages/teacher/Exams";
import { ExamNew } from "./pages/teacher/ExamNew";
import { ExamDetail } from "./pages/teacher/ExamDetail";
import { Syllabus } from "./pages/teacher/Syllabus";
import { HodDashboard } from "./pages/hod/Dashboard";
import { Releases } from "./pages/hod/Releases";
import { Audit } from "./pages/hod/Audit";
import { Security } from "./pages/hod/Security";
import { People } from "./pages/hod/People";
import { Subjects } from "./pages/hod/Subjects";
import { Account } from "./pages/Account";
import type { Role } from "./lib/types";

function Splash() {
  return (
    <div className="app-splash">
      <div className="brand-mark brand-mark-light"><span className="splash-mark">P</span></div>
      <div className="splash-copy">
        <strong>Praxis</strong>
        <span>Establishing secure session…</span>
      </div>
    </div>
  );
}

const HOME: Record<Role, string> = { HOD: "/hod", TEACHER: "/teacher", STUDENT: "/student" };

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Splash />;
  if (!user) return <Navigate to="/login" replace />;
  // A temporary password must be replaced before anything else; the API enforces this too.
  if (user.must_change_password && location.pathname !== "/account") {
    return <Navigate to="/account" replace />;
  }
  return <>{children}</>;
}

/** Route guard. The HOD is a superset of a teacher, so staff routes list both roles. */
function RequireRole({ role, children }: { role: Role | Role[]; children: ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  const allowed = Array.isArray(role) ? role : [role];
  if (!allowed.includes(user.role)) {
    return <Navigate to={HOME[user.role]} replace />;
  }
  return <>{children}</>;
}

const STAFF: Role[] = ["TEACHER", "HOD"];

function HomeRedirect() {
  const { user, loading } = useAuth();
  if (loading) return <Splash />;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={HOME[user.role]} replace />;
}

function NotFound() {
  return (
    <div className="not-found">
      <span className="eyebrow">404</span>
      <h1>This page is not part of the assessment.</h1>
      <p>The route may have moved, or your role does not include it.</p>
      <a className="btn btn-primary btn-md" href="/">Return to your workspace</a>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              element={
                <Protected>
                  <AppShell />
                </Protected>
              }
            >
              <Route path="/" element={<HomeRedirect />} />
              <Route path="/student" element={<RequireRole role="STUDENT"><StudentDashboard /></RequireRole>} />
              <Route path="/student/practice" element={<RequireRole role="STUDENT"><PracticeList /></RequireRole>} />
              <Route path="/student/practice/:testId" element={<RequireRole role="STUDENT"><PracticeDetail /></RequireRole>} />
              <Route path="/student/attempt/:attemptId" element={<RequireRole role="STUDENT"><AttemptPage /></RequireRole>} />
              <Route path="/student/results/:attemptId" element={<RequireRole role="STUDENT"><Result /></RequireRole>} />
              <Route path="/student/analytics" element={<RequireRole role="STUDENT"><Analytics /></RequireRole>} />
              <Route path="/student/history" element={<RequireRole role="STUDENT"><History /></RequireRole>} />

              <Route path="/teacher" element={<RequireRole role="TEACHER"><TeacherDashboard /></RequireRole>} />
              <Route path="/teacher/practice" element={<RequireRole role={STAFF}><PracticeStudio /></RequireRole>} />
              <Route path="/teacher/practice/new" element={<RequireRole role={STAFF}><PracticeNew /></RequireRole>} />
              <Route path="/teacher/practice/:testId" element={<RequireRole role={STAFF}><TeacherPracticeDetail /></RequireRole>} />
              <Route path="/teacher/exams" element={<RequireRole role={STAFF}><Exams /></RequireRole>} />
              <Route path="/teacher/exams/new" element={<RequireRole role={STAFF}><ExamNew /></RequireRole>} />
              <Route path="/teacher/exams/:examId" element={<RequireRole role={STAFF}><ExamDetail /></RequireRole>} />
              <Route path="/teacher/syllabus" element={<RequireRole role={STAFF}><Syllabus /></RequireRole>} />

              <Route path="/hod" element={<RequireRole role="HOD"><HodDashboard /></RequireRole>} />
              <Route path="/hod/releases" element={<RequireRole role="HOD"><Releases /></RequireRole>} />
              <Route path="/hod/audit" element={<RequireRole role="HOD"><Audit /></RequireRole>} />
              <Route path="/hod/security" element={<RequireRole role="HOD"><Security /></RequireRole>} />
              <Route path="/hod/people" element={<RequireRole role="HOD"><People /></RequireRole>} />
              <Route path="/hod/subjects" element={<RequireRole role="HOD"><Subjects /></RequireRole>} />
              <Route path="/account" element={<Account />} />
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
