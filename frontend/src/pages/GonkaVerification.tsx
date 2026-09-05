import { useState } from "react";
import { api, type GonkaVerificationResult } from "../lib/api";

const panelStyle: React.CSSProperties = {
  background: "var(--paper-elevated)",
  border: "1px solid var(--glass-border)",
  borderRadius: 18,
  boxShadow: "var(--shadow-card)",
  backdropFilter: "blur(14px) saturate(115%)",
  WebkitBackdropFilter: "blur(14px) saturate(115%)",
};

const labelStyle: React.CSSProperties = {
  color: "var(--accent-gold)",
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  fontWeight: 700,
  letterSpacing: "0.2em",
  textTransform: "uppercase",
};

export default function GonkaVerification() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<GonkaVerificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function verify() {
    const claim = text.trim();
    if (!claim) {
      setError("Please enter a claim to verify.");
      setResult(null);
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.gonkaVerify(claim));
    } catch {
      setError("Unable to complete Gonka verification. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <article style={{ display: "grid", gap: "var(--s-6)", maxWidth: 960 }}>
      <header style={{ display: "grid", gap: "var(--s-3)" }}>
        <span style={labelStyle}>VeriLens · GonkaRouter</span>
        <h1>Gonka AI Verification</h1>
        <p style={{ maxWidth: "65ch", fontSize: "1.1rem" }}>
          Test a financial claim with a concise AI-assisted review. The model
          evaluates only the text you provide and identifies where the claim
          remains uncertain.
        </p>
      </header>

      <section style={{ ...panelStyle, padding: "var(--s-6)", display: "grid", gap: "var(--s-4)" }}>
        <label htmlFor="gonka-claim" style={labelStyle}>Claim or statement</label>
        <textarea
          id="gonka-claim"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Paste a financial claim or statement to verify..."
          rows={8}
          maxLength={12000}
          style={{
            width: "100%", boxSizing: "border-box", resize: "vertical",
            padding: "var(--s-4)", border: "1px solid var(--rule-soft)",
            borderRadius: 12, background: "rgba(255,255,255,0.68)",
            color: "var(--ink)", font: "inherit", lineHeight: 1.55,
          }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--s-4)", flexWrap: "wrap" }}>
          <span style={{ color: "var(--ink-4)", fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)" }}>{text.length.toLocaleString()} / 12,000</span>
          <button
            type="button"
            onClick={verify}
            disabled={loading}
            style={{
              padding: "var(--s-3) var(--s-5)", border: 0, borderRadius: 10,
              background: "var(--ink)", color: "var(--paper)", cursor: loading ? "wait" : "pointer",
              font: "inherit", fontWeight: 700, letterSpacing: "0.08em",
            }}
          >
            {loading ? "Verifying with Gonka..." : "Verify with Gonka"}
          </button>
        </div>
        {error && <p role="alert" style={{ color: "var(--risk-critical)", margin: 0 }}>{error}</p>}
      </section>

      {result && (
        <section style={{ display: "grid", gap: "var(--s-5)" }} aria-live="polite">
          <div style={{ ...panelStyle, padding: "var(--s-6)", display: "grid", gap: "var(--s-3)" }}>
            <span style={labelStyle}>Truth Score</span>
            <strong style={{ color: "var(--ink)", fontFamily: "var(--font-mono)", fontSize: "clamp(3rem, 8vw, 5.5rem)", lineHeight: 1 }}>{result.truth_score} <small style={{ fontSize: "1rem", color: "var(--ink-3)" }}>/ 100</small></strong>
            <span style={{ color: "var(--accent-gold)", fontWeight: 700, fontSize: "1.15rem" }}>{result.verdict}</span>
          </div>
          <div style={{ ...panelStyle, padding: "var(--s-6)", display: "grid", gap: "var(--s-5)" }}>
            <div><span style={labelStyle}>Reasoning</span><p style={{ marginTop: "var(--s-2)" }}>{result.reasoning}</p></div>
            <div><span style={labelStyle}>Evidence / Indicators</span><ul style={{ margin: "var(--s-2) 0 0", paddingLeft: "1.2rem", color: "var(--ink-2)", lineHeight: 1.6 }}>{result.evidence.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
            <div><span style={labelStyle}>Caveats</span><ul style={{ margin: "var(--s-2) 0 0", paddingLeft: "1.2rem", color: "var(--ink-2)", lineHeight: 1.6 }}>{result.caveats.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
            <div style={{ display: "grid", gap: "var(--s-2)", borderTop: "1px solid var(--rule-soft)", paddingTop: "var(--s-4)" }}>
              <span style={labelStyle}>Confidence</span><strong style={{ fontFamily: "var(--font-mono)", color: "var(--ink)" }}>{Math.round(result.confidence * 100)}%</strong>
              <span style={labelStyle}>Gonka Request ID</span><code style={{ overflowWrap: "anywhere", color: "var(--ink-2)" }}>{result.gonka_request_id}</code>
              <span style={{ color: "var(--risk-low)", fontWeight: 700 }}>{result.status}</span>
            </div>
          </div>
        </section>
      )}
    </article>
  );
}
