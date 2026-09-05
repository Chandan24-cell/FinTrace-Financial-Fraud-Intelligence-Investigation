import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { HttpError, downloadReport, BAND_PALETTE } from "../lib/api";
import { DEMO_CASES } from "../lib/demoCases";

const eyebrow: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.28em",
  textTransform: "uppercase",
  color: "var(--accent-gold)",
  fontWeight: 700,
};

const cardStyle: React.CSSProperties = {
  background: "var(--paper-elevated)",
  padding: "var(--s-6)",
  borderTop: "1px solid var(--rule)",
  borderBottom: "1px solid var(--rule-soft)",
  boxShadow: "var(--shadow-card)",
};

const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "var(--s-3) var(--s-4)",
  border: "1px solid var(--rule-soft)",
  borderRadius: 0,
  background: "var(--paper)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--t-meta)",
  outline: "none",
};

const primaryBtn: React.CSSProperties = {
  padding: "var(--s-3) var(--s-5)",
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

const chipBtn: React.CSSProperties = {
  padding: "var(--s-2) var(--s-3)",
  background: "var(--paper)",
  color: "var(--ink-2)",
  border: "1px solid var(--rule-soft)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.06em",
  cursor: "pointer",
  borderRadius: 0,
};

interface ReportLog {
  cin: string;
  reportId: string | null;
  generatedAt: string | null;
}

export default function Reports() {
  const navigate = useNavigate();
  // No demo fallback — analyst types the target CIN.
  const [cin, setCin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<ReportLog[]>([]);

  const trigger = async (target: string) => {
    setBusy(true);
    setError(null);
    try {
      const meta = await downloadReport(target);
      setLog((prev) => [{ cin: target, ...meta }, ...prev].slice(0, 10));
    } catch (e) {
      // Stream 1.5 — JWT expired / missing on /report/{cin}: send the user
      // to /login with a `next` hint so they land back here after sign-in.
      // Anything else (403/404/422/5xx) renders inline so the analyst can
      // see what went wrong without losing the page state.
      if (e instanceof HttpError && e.status === 401) {
        navigate(`/login?next=${encodeURIComponent("/reports")}`, { replace: true });
        return;
      }
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: "var(--s-6)", maxWidth: 960 }}>
      <header style={{ borderBottom: "1px solid var(--rule)", paddingBottom: "var(--s-5)" }}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
          Evidence Export · PRD §7.1 Dual-Output Dossier
        </p>
        <h1 style={{
          fontFamily: "var(--font-display)",
          fontSize: "var(--t-h1)",
          fontWeight: 500,
          color: "var(--ink)",
          margin: 0,
          letterSpacing: "-0.01em",
        }}>
          Reports
        </h1>
        <div style={{ height: 1, background: "var(--accent-gold)", width: 56, margin: "var(--s-4) 0" }} aria-hidden />
        <p style={{
          color: "var(--ink-2)",
          margin: 0,
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-body)",
          maxWidth: "60ch",
          lineHeight: 1.6,
        }}>
          Render the dual-output as a single-page PDF — UUID + UTC timestamp +
          disclaimer baked in. Restricted to auditor / investigator / admin
          roles; credit-officer accounts cannot export dossiers.
        </p>
      </header>

      {/* Quick-render chips — shown when no past reports exist. One click
          downloads the dossier for a verified case so a new analyst sees
          the PDF format immediately. */}
      {log.length === 0 && (
        <article style={{ ...cardStyle, borderLeft: "4px solid var(--accent-gold)" }}>
          <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
            First dossier · click to render
          </p>
          <p style={{
            color: "var(--ink-3)", margin: 0, marginBottom: "var(--s-4)",
            fontFamily: "var(--font-body)", fontSize: "var(--t-meta)",
            maxWidth: "62ch", lineHeight: 1.55,
          }}>
            One click renders the dual-output PDF — score, calibrated
            probability, evidence chain, signed disclaimer. Each case
            below produces a 6-8 page dossier.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
            {DEMO_CASES.filter((d) => /^[LU]/.test(d.cin)).map((d) => {
              const palette = BAND_PALETTE[d.band];
              return (
                <button
                  key={d.key}
                  type="button"
                  disabled={busy}
                  onClick={() => trigger(d.cin)}
                  style={{
                    display: "inline-flex", alignItems: "baseline", gap: 8,
                    padding: "var(--s-2) var(--s-4)",
                    background: "var(--paper)",
                    border: "1px solid var(--rule-soft)",
                    borderLeft: `3px solid ${palette.bg}`,
                    cursor: busy ? "wait" : "pointer", borderRadius: 0,
                    fontFamily: "var(--font-body)",
                  }}
                >
                  <span style={{ fontWeight: 700, color: "var(--ink)", fontSize: "0.9rem" }}>{d.name}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-3)", fontSize: "0.72rem" }}>
                    {busy ? "rendering…" : `${d.evidence} signals → PDF`}
                  </span>
                </button>
              );
            })}
          </div>
        </article>
      )}

      <article style={cardStyle}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-4)" }}>Render</p>
        <form
          onSubmit={(e) => { e.preventDefault(); trigger(cin.trim().toUpperCase()); }}
          style={{ display: "flex", gap: "var(--s-3)", alignItems: "stretch" }}
        >
          <input
            id="report-cin"
            value={cin}
            onChange={(e) => setCin(e.target.value)}
            placeholder="Paste a CIN or use a verified-case chip above"
            aria-label="CIN to render"
            style={inputStyle}
          />
          <button type="submit" disabled={busy} style={primaryBtn}>
            {busy ? "Rendering…" : "Download PDF"}
          </button>
        </form>

        {error && (
          <p style={{
            color: "var(--risk-critical)",
            marginTop: "var(--s-5)",
            marginBottom: 0,
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-meta)",
            paddingLeft: "var(--s-3)",
            borderLeft: "2px solid var(--risk-critical)",
          }}>{error}</p>
        )}
      </article>

      {log.length > 0 && (
        <article style={cardStyle}>
          <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-4)" }}>Recently exported</p>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{
                textAlign: "left",
                borderBottom: "1px solid var(--rule)",
              }}>
                <th style={{
                  padding: "var(--s-3) 0",
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--t-eyebrow)",
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: "var(--ink-3)",
                  fontWeight: 600,
                }}>CIN</th>
                <th style={{
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--t-eyebrow)",
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: "var(--ink-3)",
                  fontWeight: 600,
                }}>Report ID</th>
                <th style={{
                  fontFamily: "var(--font-body)",
                  fontSize: "var(--t-eyebrow)",
                  letterSpacing: "0.18em",
                  textTransform: "uppercase",
                  color: "var(--ink-3)",
                  fontWeight: 600,
                }}>Generated (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {log.map((row, i) => (
                <tr key={`${row.reportId}-${i}`} style={{ borderBottom: "1px solid var(--rule-soft)" }}>
                  <td style={{ padding: "var(--s-3) 0", fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)", color: "var(--ink)" }}>
                    {row.cin}
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-eyebrow)", color: "var(--ink-2)" }}>
                    {row.reportId ?? "—"}
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)", color: "var(--ink-2)" }}>
                    {row.generatedAt ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      )}
    </div>
  );
}
