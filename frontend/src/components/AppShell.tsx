import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BookOpenCheck,
  ClipboardList,
  FileLock2,
  GraduationCap,
  LayoutDashboard,
  Library,
  LogOut,
  Menu,
  ScanLine,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { initials } from "../lib/format";
import { Badge } from "./ui";

const NAV = {
  STUDENT: [
    { to: "/student", label: "Overview", icon: LayoutDashboard, end: true },
    { to: "/student/practice", label: "Practice", icon: BookOpenCheck },
    { to: "/student/analytics", label: "Analytics", icon: Activity },
    { to: "/student/history", label: "History", icon: ClipboardList },
  ],
  TEACHER: [
    { to: "/teacher", label: "Overview", icon: LayoutDashboard, end: true },
    { to: "/teacher/practice", label: "Practice studio", icon: BookOpenCheck },
    { to: "/teacher/exams", label: "Examinations", icon: FileLock2 },
    { to: "/teacher/syllabus", label: "Syllabus library", icon: Library },
  ],
  HOD: [
    { to: "/hod", label: "Command centre", icon: LayoutDashboard, end: true },
    { to: "/hod/releases", label: "Release control", icon: ScanLine },
    { to: "/hod/audit", label: "Audit trail", icon: ClipboardList },
    { to: "/hod/security", label: "Security events", icon: ShieldCheck },
  ],
};

const ROLE_LABEL = {
  STUDENT: "Student workspace",
  TEACHER: "Faculty workspace",
  HOD: "Department control",
};

const CURRENT_TITLE: Record<string, string> = {
  "/student": "Overview",
  "/student/practice": "Practice",
  "/student/analytics": "Learning analytics",
  "/student/history": "Attempt history",
  "/teacher": "Overview",
  "/teacher/practice": "Practice studio",
  "/teacher/exams": "Examinations",
  "/teacher/syllabus": "Syllabus library",
  "/hod": "Command centre",
  "/hod/releases": "Release control",
  "/hod/audit": "Audit trail",
  "/hod/security": "Security events",
};

export function AppShell() {
  const { user, meta, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  if (!user) return null;
  const nav = NAV[user.role];
  const title =
    CURRENT_TITLE[location.pathname] ??
    nav.find((item) => location.pathname.startsWith(item.to) && item.to !== `/${user.role.toLowerCase()}`)
      ?.label ??
    "Praxis";

  const signOut = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark">
            <GraduationCap size={21} />
          </div>
          <div>
            <strong>Praxis</strong>
            <span>Secure assessment</span>
          </div>
          <button className="sidebar-close" onClick={() => setMenuOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <div className="role-chip">
          <span className="role-dot" />
          {ROLE_LABEL[user.role]}
        </div>

        <nav className="nav">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                onClick={() => setMenuOpen(false)}
              >
                <Icon size={17} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="sidebar-note">
          <ShieldCheck size={16} />
          <div>
            <strong>AES-256-GCM</strong>
            <span>Envelope encryption · hash-chained audit</span>
          </div>
        </div>

        <div className="sidebar-user">
          <div className="avatar">{initials(user.full_name)}</div>
          <div className="sidebar-user-meta">
            <strong>{user.full_name}</strong>
            <span>{user.email}</span>
          </div>
          <button className="icon-btn icon-btn-ghost" onClick={signOut} aria-label="Sign out" title="Sign out">
            <LogOut size={17} />
          </button>
        </div>
      </aside>

      {menuOpen && <div className="sidebar-scrim" onClick={() => setMenuOpen(false)} />}

      <main className="main">
        <header className="topbar">
          <button className="menu-btn" onClick={() => setMenuOpen(true)} aria-label="Open navigation">
            <Menu size={20} />
          </button>
          <div className="topbar-title">
            <span className="topbar-eyebrow">Praxis / {ROLE_LABEL[user.role]}</span>
            <strong>{title}</strong>
          </div>
          <div className="topbar-actions">
            {meta && (
              <Badge tone={meta.ai.mode === "REAL_AI" ? "info" : "warning"} dot>
                {meta.ai.mode === "REAL_AI" ? "Live AI" : "Demo AI"}
              </Badge>
            )}
            <span className="env-chip">{meta?.env ?? "development"}</span>
            {user.mfa_enabled && (
              <span className="mfa-chip" title="Multi-factor authentication enrolled">
                <ShieldCheck size={14} /> MFA
              </span>
            )}
          </div>
        </header>

        <div className="content">
          <Outlet />
        </div>

        <footer className="app-footer">
          <span>
            <Sparkles size={13} /> Praxis v{meta?.version ?? "1.0.0"} · {meta?.encryption.algorithm ?? "AES-256-GCM"}
          </span>
          <span>Questions never exposed to faculty in plaintext</span>
        </footer>
      </main>
    </div>
  );
}
