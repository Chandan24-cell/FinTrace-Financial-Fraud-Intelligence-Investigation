import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UploadAck, UploadPreview, api, BAND_PALETTE } from "../lib/api";
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
  padding: "var(--s-2) var(--s-3)",
  border: "1px solid var(--rule-soft)",
  borderRadius: 0,
  background: "var(--paper)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--t-meta)",
  outline: "none",
};

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.18em",
  textTransform: "uppercase",
  color: "var(--ink-3)",
  fontWeight: 600,
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

const cardTitle: React.CSSProperties = {
  fontFamily: "var(--font-display)",
  fontSize: "var(--t-h3)",
  fontWeight: 500,
  color: "var(--ink)",
  margin: 0,
  marginBottom: "var(--s-2)",
  letterSpacing: "-0.005em",
};

function DcBadge({ current, projected }: { current: number; projected: number }) {
  const delta = projected - current;
  const noBump = delta === 0;
  return (
    <div style={{
      display: "inline-flex", alignItems: "baseline", gap: "var(--s-2)",
      padding: "var(--s-2) var(--s-3)",
      background: "var(--paper)",
      borderLeft: `3px solid ${noBump ? "var(--ink-4)" : "var(--accent-gold)"}`,
      marginTop: "var(--s-3)", marginBottom: "var(--s-4)",
      fontFamily: "var(--font-body)",
      fontSize: "var(--t-meta)",
    }}>
      <span style={{ ...labelStyle, color: "var(--ink-3)" }}>Data conf.</span>
      <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink)" }}>{current}%</span>
      <span style={{ color: "var(--ink-4)" }}>→</span>
      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--ink)" }}>{projected}%</span>
      <span style={{ color: noBump ? "var(--ink-4)" : "var(--accent-gold)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.08em" }}>
        {noBump ? "no change" : `+${delta} pts`}
      </span>
    </div>
  );
}

function AckBanner({ ack }: { ack: UploadAck | undefined }) {
  if (!ack) return null;
  const isOk = ack.accepted;
  return (
    <div style={{
      marginTop: "var(--s-4)",
      padding: "var(--s-3) var(--s-4)",
      background: "var(--paper)",
      borderLeft: `3px solid ${isOk ? "var(--risk-low)" : "var(--risk-critical)"}`,
    }}>
      <p style={{
        ...labelStyle,
        color: isOk ? "var(--risk-low)" : "var(--risk-critical)",
        margin: 0, marginBottom: "var(--s-1)",
      }}>{isOk ? "Accepted" : "Rejected"}</p>
      <p style={{ margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", color: "var(--ink-2)" }}>
        {ack.detail}
      </p>
      {Object.keys(ack.extra).length > 0 && (
        <pre style={{
          margin: "var(--s-3) 0 0",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--t-eyebrow)",
          color: "var(--ink-3)",
          whiteSpace: "pre-wrap",
        }}>
          {JSON.stringify(ack.extra, null, 2)}
        </pre>
      )}
    </div>
  );
}

function ErrorLine({ message }: { message: string }) {
  return (
    <p style={{
      color: "var(--risk-critical)",
      marginTop: "var(--s-3)",
      marginBottom: 0,
      paddingLeft: "var(--s-3)",
      borderLeft: "2px solid var(--risk-critical)",
      fontFamily: "var(--font-body)",
      fontSize: "var(--t-meta)",
    }}>{message}</p>
  );
}

function FinancialsForm({ cin, preview, onUploaded }: {
  cin: string; preview: UploadPreview | undefined; onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const mutation = useMutation({
    mutationFn: () => api.uploadFinancials(cin, file!),
    onSuccess: onUploaded,
  });
  return (
    <article style={cardStyle}>
      <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>1 · AOC-4 PDF</p>
      <h2 style={cardTitle}>Financial statement</h2>
      <p style={{ color: "var(--ink-3)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", maxWidth: "60ch" }}>
        pdfplumber pulls the FS row + forensics into the per-CIN overlay
        (Day-7 hardened parser + Day-11 paren-negative / crore-unit support).
      </p>
      {preview && (
        <DcBadge current={preview.current_data_confidence} projected={preview.if_financials_added} />
      )}
      <form
        onSubmit={(e) => { e.preventDefault(); if (file) mutation.mutate(); }}
        style={{ display: "flex", gap: "var(--s-3)", alignItems: "center", marginTop: "var(--s-3)" }}
      >
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          style={{ ...inputStyle, padding: "var(--s-2)" }}
        />
        <button type="submit" disabled={!file || mutation.isPending} style={primaryBtn}>
          {mutation.isPending ? "Uploading…" : "Upload"}
        </button>
      </form>
      {mutation.error && <ErrorLine message={(mutation.error as Error).message} />}
      <AckBanner ack={mutation.data} />
    </article>
  );
}

function GstForm({ cin, preview, onUploaded }: {
  cin: string; preview: UploadPreview | undefined; onUploaded: () => void;
}) {
  const [gstin, setGstin] = useState("27AAACX1234A1Z5");
  const [pan, setPan] = useState("AAACX1234A");
  const [turnover, setTurnover] = useState("100000");
  const mutation = useMutation({
    mutationFn: () => api.uploadGst(cin, {
      gstin, pan,
      cin,
      registration_date: "2019-04-01",
      is_cancelled: false,
      taxpayer_type: "regular",
      aggregate_turnover: Number(turnover),
      tax_paid_ytd: 0,
    }),
    onSuccess: onUploaded,
  });
  return (
    <article style={cardStyle}>
      <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>2 · GST entity</p>
      <h2 style={cardTitle}>GST overlay</h2>
      <p style={{ color: "var(--ink-3)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", maxWidth: "60ch" }}>
        Triggers Module 2 #1 (Revenue vs GST Turnover) when the upload diverges
        &gt; 5% from the company's P&amp;L revenue.
      </p>
      {preview && (
        <DcBadge current={preview.current_data_confidence} projected={preview.if_gst_added} />
      )}
      <form
        onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }}
        style={{
          display: "grid",
          gridTemplateColumns: "120px 1fr",
          gap: "var(--s-3)",
          alignItems: "center",
          marginTop: "var(--s-3)",
        }}
      >
        <label style={labelStyle}>GSTIN</label>
        <input value={gstin} onChange={(e) => setGstin(e.target.value)} style={inputStyle} />
        <label style={labelStyle}>PAN</label>
        <input value={pan} onChange={(e) => setPan(e.target.value)} style={inputStyle} />
        <label style={labelStyle}>Aggregate turnover ₹</label>
        <input value={turnover} onChange={(e) => setTurnover(e.target.value)} style={inputStyle} />
        <span />
        <button type="submit" disabled={mutation.isPending} style={{ ...primaryBtn, justifySelf: "start" }}>
          {mutation.isPending ? "Submitting…" : "Submit GST overlay"}
        </button>
      </form>
      {mutation.error && <ErrorLine message={(mutation.error as Error).message} />}
      <AckBanner ack={mutation.data} />
    </article>
  );
}

function BankForm({ cin, preview, onUploaded }: {
  cin: string; preview: UploadPreview | undefined; onUploaded: () => void;
}) {
  const [credits, setCredits] = useState("250000000");
  const mutation = useMutation({
    mutationFn: () => api.uploadBank(cin, Number(credits)),
    onSuccess: onUploaded,
  });
  // PRD §7.1 ladder: bank only bumps DC once GST is on file. Surface the
  // dependency so the analyst doesn't think the endpoint is broken.
  const bankNeedsGst =
    preview !== undefined &&
    preview.if_bank_added === preview.current_data_confidence &&
    !preview.state.has_gst_upload;
  return (
    <article style={cardStyle}>
      <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>3 · Bank credits</p>
      <h2 style={cardTitle}>Bank credits total</h2>
      <p style={{ color: "var(--ink-3)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", maxWidth: "60ch" }}>
        Triggers Module 2 #7 (Bank Credits vs Revenue) when the reconstructed
        bank credits diverge &gt; 20% from the company's P&amp;L revenue.
      </p>
      {preview && (
        <DcBadge current={preview.current_data_confidence} projected={preview.if_bank_added} />
      )}
      {bankNeedsGst && (
        <p style={{
          color: "var(--risk-high)",
          margin: "var(--s-2) 0 var(--s-3)",
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.04em",
        }}>
          Note: PRD §7.1 ladder requires GST overlay before bank evidence counts
          toward DC. Submit anyway — Module 2 #7 still fires — but DC won't move
          until GST is on file.
        </p>
      )}
      <form
        onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }}
        style={{ display: "flex", gap: "var(--s-3)", alignItems: "center", marginTop: "var(--s-3)" }}
      >
        <input value={credits} onChange={(e) => setCredits(e.target.value)} style={{ ...inputStyle, flex: 1 }} />
        <button type="submit" disabled={mutation.isPending} style={primaryBtn}>
          {mutation.isPending ? "Submitting…" : "Submit bank total"}
        </button>
      </form>
      {mutation.error && <ErrorLine message={(mutation.error as Error).message} />}
      <AckBanner ack={mutation.data} />
    </article>
  );
}

function PreviewCard({ preview }: { preview: UploadPreview | undefined }) {
  if (!preview) return null;
  const { state, current_data_confidence } = preview;
  return (
    <article style={cardStyle}>
      <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>Overlay state</p>
      <h2 style={cardTitle}>
        Current DC <span style={{ color: "var(--accent-gold)" }}>{current_data_confidence}%</span>
      </h2>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: "var(--s-5)",
        marginTop: "var(--s-3)",
        paddingTop: "var(--s-4)",
        borderTop: "1px solid var(--rule-soft)",
      }}>
        <div>
          <p style={labelStyle}>Financials</p>
          <p style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-h3)", color: "var(--ink)", margin: "var(--s-1) 0 0" }}>
            {state.n_financials}
          </p>
        </div>
        <div>
          <p style={labelStyle}>GST overlay</p>
          <p style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-h3)",
            color: state.has_gst_upload ? "var(--risk-low)" : "var(--ink-4)",
            margin: "var(--s-1) 0 0",
          }}>
            {state.has_gst_upload ? "yes" : "—"}
          </p>
        </div>
        <div>
          <p style={labelStyle}>Bank overlay</p>
          <p style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-h3)",
            color: state.has_bank_upload ? "var(--risk-low)" : "var(--ink-4)",
            margin: "var(--s-1) 0 0",
          }}>
            {state.has_bank_upload ? "yes" : "—"}
          </p>
        </div>
      </div>
    </article>
  );
}

export default function UploadPage() {
  // No demo fallback — analyst types the target CIN, or arrives with it
  // pasted in from the Search page. Empty state until they enter one.
  const [cin, setCin] = useState("");
  const qc = useQueryClient();
  const previewQuery = useQuery({
    queryKey: ["upload-preview", cin],
    queryFn: () => api.uploadPreview(cin),
    enabled: cin.length > 0,
  });
  const refetchPreview = () => {
    qc.invalidateQueries({ queryKey: ["upload-preview", cin] });
  };
  return (
    <div style={{ display: "grid", gap: "var(--s-6)", maxWidth: 960 }}>
      <header style={{ borderBottom: "1px solid var(--rule)", paddingBottom: "var(--s-5)" }}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
          Evidence Intake · PRD §7.1 DataConfidence Ladder
        </p>
        <h1 style={{
          fontFamily: "var(--font-display)",
          fontSize: "var(--t-h1)",
          fontWeight: 500,
          color: "var(--ink)",
          margin: 0,
          letterSpacing: "-0.01em",
        }}>
          Upload evidence
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
          Three /upload/* endpoints stash overlays for the next /analyse request.
          Each form shows the DataConfidence bump this upload would buy
          (Day-21 preview).
        </p>
      </header>

      {/* Demo-case chips — shown when no CIN is typed. Click any to pre-fill
          the target so a new user immediately sees what the upload flow
          does without having to hunt for a valid CIN. Hidden once typing
          begins so the analyst's input remains the source of truth. */}
      {cin.length === 0 && (
        <article style={{ ...cardStyle, borderLeft: "4px solid var(--accent-gold)" }}>
          <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
            Start with a verified case
          </p>
          <p style={{
            color: "var(--ink-3)", margin: 0, marginBottom: "var(--s-4)",
            fontFamily: "var(--font-body)", fontSize: "var(--t-meta)",
            maxWidth: "62ch", lineHeight: 1.55,
          }}>
            Click any case below to pre-fill its CIN — the three upload
            forms unlock and you can drop a real AOC-4 / GST / bank file
            to see how the overlay bumps the DataConfidence ladder.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap" }}>
            {DEMO_CASES.filter((d) => /^[LU]/.test(d.cin)).map((d) => {
              const palette = BAND_PALETTE[d.band];
              return (
                <button
                  key={d.key}
                  type="button"
                  onClick={() => setCin(d.cin)}
                  style={{
                    display: "inline-flex", alignItems: "baseline", gap: 8,
                    padding: "var(--s-2) var(--s-4)",
                    background: "var(--paper)",
                    border: "1px solid var(--rule-soft)",
                    borderLeft: `3px solid ${palette.bg}`,
                    cursor: "pointer", borderRadius: 0,
                    fontFamily: "var(--font-body)",
                  }}
                >
                  <span style={{ fontWeight: 700, color: "var(--ink)", fontSize: "0.9rem" }}>{d.name}</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-3)", fontSize: "0.72rem" }}>{d.cin}</span>
                </button>
              );
            })}
          </div>
        </article>
      )}

      <article style={cardStyle}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-3)" }}>Target CIN</p>
        <input
          id="upload-cin"
          value={cin}
          onChange={(e) => setCin(e.target.value.trim().toUpperCase())}
          placeholder="Paste a CIN or click a verified case above"
          style={{ ...inputStyle, width: "100%", boxSizing: "border-box" }}
        />
      </article>

      {previewQuery.error && (
        <article style={{ ...cardStyle, borderLeft: "4px solid var(--risk-critical)" }}>
          <p style={{ ...labelStyle, color: "var(--risk-critical)", margin: 0, marginBottom: "var(--s-1)" }}>Preview failed</p>
          <p style={{ color: "var(--ink)", margin: 0, fontFamily: "var(--font-body)" }}>
            {(previewQuery.error as Error).message}
          </p>
        </article>
      )}
      <PreviewCard preview={previewQuery.data} />
      <FinancialsForm cin={cin} preview={previewQuery.data} onUploaded={refetchPreview} />
      <GstForm cin={cin} preview={previewQuery.data} onUploaded={refetchPreview} />
      <BankForm cin={cin} preview={previewQuery.data} onUploaded={refetchPreview} />
    </div>
  );
}
