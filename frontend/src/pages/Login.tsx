import { useState } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { HttpError } from "../lib/api";
import { useAuth } from "../lib/auth";

// ── Styles ──────────────────────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  background: "var(--paper-elevated)",
  padding: "var(--s-7) var(--s-6) var(--s-6)",
  boxShadow: "var(--shadow-card)",
  maxWidth: 460,
  margin: "var(--s-9) auto 0",
  borderTop: "1px solid var(--rule)",
  borderBottom: "1px solid var(--rule-soft)",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.18em",
  textTransform: "uppercase",
  color: "var(--ink-3)",
  marginBottom: "var(--s-2)",
  fontWeight: 600,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--s-3) var(--s-4)",
  border: "1px solid var(--rule-soft)",
  borderRadius: 0,
  background: "var(--paper)",
  color: "var(--ink)",
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-body)",
  marginBottom: "var(--s-5)",
  outline: "none",
  boxSizing: "border-box",
};

const btnStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--s-4) var(--s-5)",
  border: 0,
  background: "var(--ink)",
  color: "var(--paper)",
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.22em",
  textTransform: "uppercase",
  fontWeight: 700,
  cursor: "pointer",
  borderRadius: 0,
};

// ── Role definitions ─────────────────────────────────────────────────────────

type RoleKey = "credit_officer" | "investigator" | "auditor" | "admin";

interface RoleMeta {
  key: RoleKey;
  label: string;
  description: string;
  access: string;
}

// Admin is excluded — the role can only be granted by a seeding script, not via self-registration.
const ROLES: RoleMeta[] = [
  {
    key: "credit_officer",
    label: "Loan Officer",
    description: "Bank or NBFC credit analyst evaluating borrower risk",
    access: "Search · Dashboard · Graph · Upload · Reports",
  },
  {
    key: "investigator",
    label: "Fraud Investigator",
    description: "DGGI / ED officer investigating ITC or loan fraud",
    access: "Full access — including ITC Ring and Evergreening views",
  },
  {
    key: "auditor",
    label: "Auditor",
    description: "Statutory or internal auditor reviewing compliance",
    access: "Read-only: Dashboard · Evidence chain · Reports",
  },
];

// ── Demo accounts ─────────────────────────────────────────────────────────────

interface DemoAccount {
  persona: string;
  email: string;
  password: string;
  roleLabel: string;
}

const DEMO_ACCOUNTS: DemoAccount[] = [
  { persona: "Priya Sharma",   email: "priya@demo.in", password: "Sentinel@1", roleLabel: "Loan Officer" },
  { persona: "Rajan Mehta",    email: "rajan@demo.in", password: "Sentinel@1", roleLabel: "Fraud Investigator" },
  { persona: "Deepa Krishnan", email: "deepa@demo.in", password: "Sentinel@1", roleLabel: "Auditor" },
  { persona: "Amir Khan",      email: "amir@demo.in",  password: "Sentinel@1", roleLabel: "Admin" },
];

// ── Role card ────────────────────────────────────────────────────────────────

function RoleCard({ meta, selected, onSelect }: {
  meta: RoleMeta;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      style={{
        padding: "var(--s-4)",
        background: selected ? "var(--paper)" : "transparent",
        border: selected ? "1.5px solid var(--accent-gold)" : "1px solid var(--rule-soft)",
        cursor: "pointer",
        textAlign: "left",
        width: "100%",
        transition: "border-color 120ms",
      }}
    >
      <p style={{
        fontFamily: "var(--font-body)",
        fontSize: "var(--t-eyebrow)",
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        fontWeight: 700,
        color: selected ? "var(--accent-gold)" : "var(--ink)",
        margin: 0,
        marginBottom: "var(--s-1)",
      }}>
        {meta.label}
      </p>
      <p style={{
        fontFamily: "var(--font-body)",
        fontSize: "var(--t-meta)",
        color: "var(--ink-3)",
        margin: 0,
        marginBottom: "var(--s-1)",
        lineHeight: 1.4,
      }}>
        {meta.description}
      </p>
      <p style={{
        fontFamily: "var(--font-mono)",
        fontSize: "10px",
        color: selected ? "var(--accent-gold)" : "var(--ink-4)",
        margin: 0,
        letterSpacing: "0.04em",
      }}>
        {meta.access}
      </p>
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Login() {
  const { login, register, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();

  const nextParam = searchParams.get("next");
  const stateFrom = (location.state as { from?: string } | null)?.from;
  const from = nextParam || stateFrom || "/dashboard";

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [selectedRole, setSelectedRole] = useState<RoleKey>("credit_officer");
  const [error, setError] = useState<string | null>(null);
  const [emailTaken, setEmailTaken] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (isAuthenticated) return <Navigate to={from} replace />;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setEmailTaken(false);
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, selectedRole);
      }
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof HttpError && err.status === 409 && mode === "register") {
        setEmailTaken(true);
      } else {
        setError((err as Error).message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const switchToLogin = () => {
    setMode("login");
    setEmailTaken(false);
    setError(null);
    setPassword("");
  };

  // Pre-fill and switch to login mode — user still has to click Sign in
  // so they can see what was filled in and confirm before submitting.
  const fillDemo = (account: DemoAccount) => {
    setMode("login");
    setEmail(account.email);
    setPassword(account.password);
    setError(null);
    setEmailTaken(false);
  };

  return (
    <div style={{ maxWidth: 460, margin: "0 auto" }}>

      {/* ── Auth card ── */}
      <form onSubmit={onSubmit} style={cardStyle}>
        <p style={{
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.28em",
          textTransform: "uppercase",
          color: "var(--accent-gold)",
          margin: 0,
          marginBottom: "var(--s-3)",
          fontWeight: 700,
        }}>
          Sentinel-G · Analyst Console
        </p>
        <h1 style={{
          fontFamily: "var(--font-display)",
          fontWeight: 500,
          fontSize: "var(--t-h1)",
          lineHeight: 1.05,
          color: "var(--ink)",
          margin: 0,
          marginBottom: "var(--s-3)",
          letterSpacing: "-0.01em",
        }}>
          {mode === "login" ? "Sign in" : "New account"}
        </h1>
        <div style={{ height: 1, background: "var(--accent-gold)", width: 48, marginBottom: "var(--s-5)" }} aria-hidden />

        <p style={{ color: "var(--ink-3)", margin: 0, marginBottom: "var(--s-6)", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)" }}>
          {mode === "login" ? "No account?" : "Already registered?"}{" "}
          <button
            type="button"
            onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}
            style={{ background: "none", border: 0, padding: 0, color: "var(--accent-gold)", cursor: "pointer", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", fontWeight: 600, textDecoration: "underline", textUnderlineOffset: 3 }}
          >
            {mode === "login" ? "Register instead" : "Sign in instead"}
          </button>
        </p>

        <label htmlFor="email" style={labelStyle}>Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          placeholder="you@example.com"
          style={inputStyle}
        />

        <label htmlFor="password" style={labelStyle}>Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={8}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          placeholder={mode === "register" ? "At least 8 characters" : ""}
          style={inputStyle}
        />

        {/* Role selector — register only */}
        {mode === "register" && (
          <div style={{ marginBottom: "var(--s-6)" }}>
            <p style={{ ...labelStyle, marginBottom: "var(--s-3)" }}>Your role</p>
            <div style={{ display: "grid", gap: "var(--s-2)" }}>
              {ROLES.map((r) => (
                <RoleCard
                  key={r.key}
                  meta={r}
                  selected={selectedRole === r.key}
                  onSelect={() => setSelectedRole(r.key)}
                />
              ))}
            </div>
            <p style={{
              fontFamily: "var(--font-body)",
              fontSize: "var(--t-meta)",
              color: "var(--ink-4)",
              margin: "var(--s-3) 0 0",
              lineHeight: 1.5,
            }}>
              Your role determines which pages and actions are available to you.
              It cannot be changed after registration without admin assistance.
            </p>
          </div>
        )}

        {emailTaken && (
          <div role="alert" style={{ marginBottom: "var(--s-5)", padding: "var(--s-3) var(--s-4)", background: "var(--paper)", borderLeft: "3px solid var(--accent-gold)", color: "var(--ink-2)", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", lineHeight: 1.5 }}>
            That email is already registered.{" "}
            <button type="button" onClick={switchToLogin} style={{ background: "none", border: 0, padding: 0, color: "var(--accent-gold)", cursor: "pointer", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", fontWeight: 700, textDecoration: "underline", textUnderlineOffset: 3 }}>
              Sign in instead?
            </button>
          </div>
        )}

        {error && (
          <div style={{ marginBottom: "var(--s-5)", padding: "var(--s-3) var(--s-4)", background: "var(--paper)", borderLeft: "3px solid var(--risk-critical)", color: "var(--risk-critical)", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)" }}>
            {error}
          </div>
        )}

        <button type="submit" disabled={submitting} style={{ ...btnStyle, opacity: submitting ? 0.6 : 1 }}>
          {submitting ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>

      {/* ── Demo accounts panel ── */}
      <div style={{
        background: "var(--paper-elevated)",
        borderTop: "1px solid var(--rule)",
        borderBottom: "1px solid var(--rule-soft)",
        boxShadow: "var(--shadow-card)",
        padding: "var(--s-5) var(--s-6)",
        marginBottom: "var(--s-9)",
      }}>
        <p style={{
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.28em",
          textTransform: "uppercase",
          color: "var(--accent-gold)",
          fontWeight: 700,
          margin: 0,
          marginBottom: "var(--s-3)",
        }}>
          Demo access
        </p>
        <p style={{
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-meta)",
          color: "var(--ink-3)",
          margin: "0 0 var(--s-4)",
          lineHeight: 1.5,
        }}>
          First run:{" "}
          <code style={{ fontFamily: "var(--font-mono)", background: "var(--paper)", padding: "1px 6px", fontSize: "11px", letterSpacing: "0.02em" }}>
            uv run scripts/seed_users.py
          </code>
          {" "}— then click any row below to pre-fill the form.
          All accounts share password{" "}
          <code style={{ fontFamily: "var(--font-mono)", background: "var(--paper)", padding: "1px 6px", fontSize: "11px" }}>
            Sentinel@1
          </code>.
        </p>

        <div style={{ display: "grid", gap: "var(--s-2)" }}>
          {DEMO_ACCOUNTS.map((acc) => (
            <button
              key={acc.email}
              type="button"
              onClick={() => fillDemo(acc)}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                gap: "var(--s-4)",
                alignItems: "center",
                padding: "var(--s-3) var(--s-4)",
                background: "var(--paper)",
                border: "1px solid var(--rule-soft)",
                cursor: "pointer",
                textAlign: "left",
                width: "100%",
              }}
            >
              <div>
                <p style={{ fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", fontWeight: 600, color: "var(--ink)", margin: 0 }}>
                  {acc.persona}
                </p>
                <p style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--ink-3)", margin: "2px 0 0", letterSpacing: "0.02em" }}>
                  {acc.email}
                </p>
              </div>
              <span style={{
                fontFamily: "var(--font-body)",
                fontSize: "10px",
                letterSpacing: "0.14em",
                textTransform: "uppercase",
                fontWeight: 700,
                color: "var(--accent-gold)",
                whiteSpace: "nowrap",
                paddingLeft: "var(--s-3)",
                borderLeft: "1px solid var(--rule-soft)",
              }}>
                {acc.roleLabel}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
