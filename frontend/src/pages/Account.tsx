import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound, ShieldCheck, ShieldOff, Smartphone } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { api, errorMessage, fieldErrors } from "../lib/api";
import { formatDateTime, titleCase } from "../lib/format";
import { Badge, Button, Card, Field, Input, KeyValue, PageHeader } from "../components/ui";

interface MfaSetup {
  secret: string;
  otpauth_uri: string;
  instructions: string;
}

const sixDigits = (value: string) => value.replace(/\D/g, "").slice(0, 6);

export function Account() {
  const { user, logout, refresh } = useAuth();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [savingPassword, setSavingPassword] = useState(false);

  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [mfaBusy, setMfaBusy] = useState(false);
  const [mfaError, setMfaError] = useState<string | null>(null);

  if (!user) return null;
  const forced = Boolean(user.must_change_password);

  const signOutAfter = async (message: string) => {
    toast(message, "success");
    await logout();
    navigate("/login", { replace: true });
  };

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setPasswordError(null);
    if (next !== confirm) {
      setPasswordError("The new passwords do not match.");
      return;
    }
    setSavingPassword(true);
    try {
      await api("/auth/password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setCurrent("");
      setNext("");
      setConfirm("");
      await refresh();
      toast("Password updated. Other sessions were signed out.", "success");
      if (forced) navigate("/", { replace: true });
    } catch (err) {
      const fields = fieldErrors(err);
      setPasswordError(fields.length ? fields.join(" ") : errorMessage(err));
    } finally {
      setSavingPassword(false);
    }
  };

  const startMfa = async () => {
    setMfaBusy(true);
    setMfaError(null);
    try {
      setSetup(await api<MfaSetup>("/auth/mfa/setup", { method: "POST" }));
      setCode("");
    } catch (err) {
      setMfaError(errorMessage(err));
    } finally {
      setMfaBusy(false);
    }
  };

  const confirmMfa = async (event: React.FormEvent) => {
    event.preventDefault();
    setMfaBusy(true);
    setMfaError(null);
    try {
      await api("/auth/mfa/confirm", { method: "POST", body: { code } });
      await signOutAfter("Authenticator enrolled. Sign in again with your code.");
    } catch (err) {
      setMfaError(errorMessage(err));
      setMfaBusy(false);
    }
  };

  const disableMfa = async (event: React.FormEvent) => {
    event.preventDefault();
    setMfaBusy(true);
    setMfaError(null);
    try {
      await api("/auth/mfa/disable", { method: "POST", body: { code } });
      await signOutAfter("Multi-factor authentication disabled. Sign in again.");
    } catch (err) {
      setMfaError(errorMessage(err));
      setMfaBusy(false);
    }
  };

  return (
    <div className="stack">
      <PageHeader
        eyebrow="Account"
        title="Account & security"
        description="Your profile, password and sign-in protection."
      />

      {forced && (
        <div className="form-error">
          Your account is using a temporary password. Choose a new password to continue.
        </div>
      )}

      <div className="split-grid">
        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Profile</div>
              <h3>{user.full_name}</h3>
            </div>
            <Badge tone="accent">{titleCase(user.role)}</Badge>
          </div>
          <KeyValue
            items={[
              { label: "Email", value: user.email },
              { label: "Department", value: user.department },
              { label: "Last sign-in", value: user.last_login_at ? formatDateTime(user.last_login_at) : "—" },
            ]}
          />
          <p className="muted">Name and department changes are made by the Head of Department.</p>
        </Card>

        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Password</div>
              <h3>Change password</h3>
            </div>
            <KeyRound size={18} />
          </div>
          <form className="stack" onSubmit={changePassword}>
            <Field label={forced ? "Temporary password" : "Current password"}>
              <Input
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
                required
              />
            </Field>
            <Field label="New password" hint="At least 10 characters, mixing three of: lower case, upper case, digits, symbols.">
              <Input
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
                minLength={10}
                required
              />
            </Field>
            <Field label="Repeat new password">
              <Input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                required
              />
            </Field>
            {passwordError && <div className="form-error">{passwordError}</div>}
            <Button type="submit" loading={savingPassword}>
              Update password
            </Button>
          </form>
        </Card>
      </div>

      {!forced && (
        <Card>
          <div className="card-head">
            <div>
              <div className="eyebrow">Multi-factor authentication</div>
              <h3>Authenticator app</h3>
            </div>
            {user.mfa_enabled ? (
              <Badge tone="success" dot>
                Enabled
              </Badge>
            ) : (
              <Badge tone="warning" dot>
                Not enabled
              </Badge>
            )}
          </div>

          {user.role === "HOD" ? (
            <p className="muted">
              <ShieldCheck size={14} /> MFA is mandatory for the Head of Department. If you lose your device,
              another HOD can reset your authenticator from People; you will enrol a new one at your next sign-in.
            </p>
          ) : user.mfa_enabled ? (
            <form className="stack" onSubmit={disableMfa}>
              <p className="muted">
                Enter a current code from your authenticator to turn MFA off. You will be signed out.
              </p>
              <Field label="6-digit code">
                <Input
                  inputMode="numeric"
                  className="code-input"
                  value={code}
                  onChange={(event) => setCode(sixDigits(event.target.value))}
                  placeholder="000000"
                />
              </Field>
              {mfaError && <div className="form-error">{mfaError}</div>}
              <Button
                type="submit"
                variant="danger"
                loading={mfaBusy}
                disabled={code.length !== 6}
                icon={<ShieldOff size={15} />}
              >
                Disable MFA
              </Button>
            </form>
          ) : setup ? (
            <form className="stack" onSubmit={confirmMfa}>
              <div className="enroll-secret">
                <span className="field-label">Setup key (choose "enter a setup key" in your app)</span>
                <code>{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
                <a className="text-link" href={setup.otpauth_uri}>
                  Open in an authenticator on this device
                </a>
              </div>
              <Field label="Code from the app">
                <Input
                  inputMode="numeric"
                  className="code-input"
                  autoFocus
                  value={code}
                  onChange={(event) => setCode(sixDigits(event.target.value))}
                  placeholder="000000"
                />
              </Field>
              {mfaError && <div className="form-error">{mfaError}</div>}
              <Button type="submit" loading={mfaBusy} disabled={code.length !== 6}>
                Confirm and enable
              </Button>
            </form>
          ) : (
            <div className="stack">
              <p className="muted">
                Add a second step to your sign-in with an authenticator app such as Google Authenticator,
                Microsoft Authenticator or Authy.
              </p>
              {mfaError && <div className="form-error">{mfaError}</div>}
              <Button onClick={startMfa} loading={mfaBusy} icon={<Smartphone size={15} />}>
                Set up authenticator
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
