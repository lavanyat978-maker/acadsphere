import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Fingerprint,
  GraduationCap,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { api, errorMessage } from "../lib/api";
import { initials, titleCase } from "../lib/format";
import type { DemoAccount, DemoCodeResponse } from "../lib/types";
import { Button, Field, Input } from "../components/ui";
import { useToast } from "../context/ToastContext";

const HOME_BY_ROLE = { STUDENT: "/student", TEACHER: "/teacher", HOD: "/hod" };

export function Login() {
  const { user, meta, login, verifyMfa } = useAuth();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [email, setEmail] = useState("teacher@praxis.demo");
  const [password, setPassword] = useState("Praxis@2026#Demo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [demoCode, setDemoCode] = useState<DemoCodeResponse | null>(null);

  const accounts: DemoAccount[] = meta?.demo_accounts ?? [];

  if (user) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;

  const selectAccount = (account: DemoAccount) => {
    setEmail(account.email);
    setPassword(account.password);
    setError(null);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await login(email, password);
      if (result.mfa_required && result.mfa_token) {
        setChallenge(result.mfa_token);
        setCode("");
        toast("Enter the 6-digit authenticator code to continue.", "info");
      } else if (result.user) {
        navigate(HOME_BY_ROLE[result.user.role], { replace: true });
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const fetchDemoCode = async () => {
    if (!challenge) return;
    setBusy(true);
    try {
      const result = await api<DemoCodeResponse>("/auth/mfa/challenge-code", {
        method: "POST",
        body: { mfa_token: challenge },
      });
      setCode(result.code);
      setDemoCode(result);
      toast("Demo authenticator code filled in.", "info");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const submitMfa = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      const verified = await verifyMfa(challenge, code);
      navigate(HOME_BY_ROLE[verified.role], { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <section className="login-visual">
        <div className="login-brand">
          <div className="brand-mark brand-mark-light">
            <GraduationCap size={24} />
          </div>
          <div>
            <strong>Praxis</strong>
            <span>Secure Assessment Platform</span>
          </div>
        </div>

        <div className="login-hero">
          <div className="eyebrow eyebrow-light">Department of Computer Science</div>
          <h1>
            Assessment integrity,
            <br />
            <em>engineered end to end.</em>
          </h1>
          <p>
            Adaptive practice for students, syllabus-grounded question generation for faculty, and a
            sealed HOD release ceremony for internal papers.
          </p>
        </div>

        <div className="login-pillars">
          <div>
            <Sparkles size={17} />
            <strong>Grounded generation</strong>
            <span>RAG over uploaded syllabus, five validation levels, retries.</span>
          </div>
          <div>
            <LockKeyhole size={17} />
            <strong>Sealed by default</strong>
            <span>AES-256-GCM envelope encryption with wrapped data keys.</span>
          </div>
          <div>
            <Fingerprint size={17} />
            <strong>Controlled release</strong>
            <span>HOD-only, MFA-gated, time-windowed, maximum three prints.</span>
          </div>
        </div>

        <div className="login-footer">
          <ShieldCheck size={15} />
          <span>Hash-chained audit trail · RBAC on every route · CSRF-protected sessions</span>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-card-head">
            <div className="login-kicker">
              <KeyRound size={15} /> Secure sign in
            </div>
            <h2>{challenge ? "Verify your identity" : "Welcome back"}</h2>
            <p>
              {challenge
                ? "Praxis requires a time-based one-time code for HOD accounts."
                : "Use your department account. Sessions are cookie-based and CSRF protected."}
            </p>
          </div>

          {challenge ? (
            <form onSubmit={submitMfa} className="login-form">
              <div className="mfa-banner">
                <ShieldCheck size={18} />
                <div>
                  <strong>Authenticator code required</strong>
                  <span>{email}</span>
                </div>
              </div>
              <Field label="6-digit code" hint={demoCode?.warning}>
                <Input
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  autoFocus
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000000"
                  className="code-input"
                />
              </Field>
              {error && <div className="form-error">{error}</div>}
              <Button type="submit" loading={busy} disabled={code.length !== 6} icon={<ArrowRight size={16} />}>
                Verify and enter
              </Button>
              <div className="login-secondary-actions">
                <Button type="button" variant="soft" size="sm" onClick={fetchDemoCode} loading={busy}>
                  Use demo authenticator code
                </Button>
                <button
                  type="button"
                  className="text-link"
                  onClick={() => {
                    setChallenge(null);
                    setError(null);
                    setCode("");
                  }}
                >
                  Back to sign in
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={submit} className="login-form">
              <Field label="Institutional email">
                <Input
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@praxis.demo"
                  required
                />
              </Field>
              <Field label="Password">
                <Input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="••••••••••"
                  required
                />
              </Field>
              {error && <div className="form-error">{error}</div>}
              <Button type="submit" loading={busy} icon={<ArrowRight size={16} />}>
                Sign in to Praxis
              </Button>
            </form>
          )}

          {!challenge && accounts.length > 0 && (
            <div className="demo-block">
              <div className="demo-head">
                <Users size={14} />
                <span>Demo accounts · click to fill</span>
              </div>
              <div className="demo-accounts">
                {accounts.map((account) => (
                  <button
                    key={account.email}
                    type="button"
                    className={`demo-account ${email === account.email ? "selected" : ""}`}
                    onClick={() => selectAccount(account)}
                  >
                    <span className={`avatar avatar-${account.role.toLowerCase()}`}>{initials(account.full_name)}</span>
                    <span className="demo-meta">
                      <strong>{account.full_name}</strong>
                      <span>{titleCase(account.role)} · {account.email}</span>
                    </span>
                    {account.mfa === "required" && <span className="mfa-mini">MFA</span>}
                  </button>
                ))}
              </div>
              <p className="demo-note">
                Demo passwords are pre-filled. The HOD account always requires MFA — use the demo
                authenticator helper on the next step.
              </p>
            </div>
          )}

          <div className="login-security">
            <LockKeyhole size={14} />
            <span>Cookies are HttpOnly · every sign-in is audited · accounts lock after 5 failures</span>
          </div>
        </div>
      </section>
    </div>
  );
}
