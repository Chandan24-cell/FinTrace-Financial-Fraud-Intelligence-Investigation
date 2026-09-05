import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CompanySummary, type DataQuality, BAND_PALETTE } from "../lib/api";
import { DEMO_CASES, type DemoCase } from "../lib/demoCases";

const QUALITY_PALETTE: Record<DataQuality, { bg: string; fg: string; label: string }> = {
  complete:    { bg: "#15803d", fg: "#ffffff", label: "FULL DATA" },
  partial:     { bg: "#a16207", fg: "#ffffff", label: "PARTIAL" },
  master_only: { bg: "#6b6b6b", fg: "#ffffff", label: "MASTER ONLY" },
};

function QualityBadge({ q }: { q: DataQuality }) {
  const p = QUALITY_PALETTE[q];
  return (
    <span
      title={
        q === "complete"
          ? "Has financial statements + directors — full forensic analysis available"
          : q === "partial"
            ? "Has some directors or financials — limited analysis"
            : "Only public master record on file — upload financials for the full analysis"
      }
      style={{
        display: "inline-block",
        background: p.bg,
        color: p.fg,
        fontFamily: "var(--font-body)",
        fontWeight: 700,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        fontSize: "9px",
        padding: "2px 6px",
        marginTop: "var(--s-2)",
      }}
    >
      {p.label}
    </span>
  );
}

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
  padding: "var(--s-7) var(--s-6)",
  borderTop: "1px solid var(--rule)",
  borderBottom: "1px solid var(--rule-soft)",
  boxShadow: "var(--shadow-card)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "var(--s-4) var(--s-5)",
  border: "1px solid var(--rule-soft)",
  borderRadius: 0,
  background: "var(--paper)",
  color: "var(--ink)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--t-h3)",
  letterSpacing: "0.04em",
  outline: "none",
  boxSizing: "border-box",
};

const primaryBtn: React.CSSProperties = {
  padding: "var(--s-4) var(--s-6)",
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
  padding: "var(--s-3) var(--s-4)",
  background: "var(--paper)",
  color: "var(--ink-2)",
  border: "1px solid var(--rule-soft)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--t-meta)",
  letterSpacing: "0.06em",
  cursor: "pointer",
  borderRadius: 0,
  textAlign: "left",
};

const CIN_REGEX = /^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$/;

// Verified-case tile — large, click-through card for the 4 real fraud cases
// the engine catches as CRITICAL/HIGH. Sits above the manual CIN input so
// a new user never sees an "empty form" experience.
function VerifiedCaseTile({ demo, onActivate }: { demo: DemoCase; onActivate: () => void }) {
  const palette = BAND_PALETTE[demo.band];
  return (
    <button
      type="button"
      onClick={onActivate}
      style={{
        display: "grid",
        gap: "var(--s-3)",
        padding: "var(--s-5) var(--s-5) var(--s-4)",
        background: "var(--paper-elevated)",
        border: "1px solid var(--rule)",
        borderLeft: `4px solid ${palette.bg}`,
        boxShadow: "var(--shadow-card)",
        cursor: "pointer",
        textAlign: "left",
        font: "inherit",
        color: "inherit",
        borderRadius: 0,
        transition: "transform 120ms ease, box-shadow 120ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-2px)";
        e.currentTarget.style.boxShadow = "0 6px 22px rgba(0,0,0,0.10)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
        e.currentTarget.style.boxShadow = "var(--shadow-card)";
      }}
    >
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-3)" }}>
        <span style={{
          fontFamily: "var(--font-display)", fontWeight: 500,
          fontSize: "1.5rem", color: "var(--ink)", lineHeight: 1.1,
        }}>{demo.name}</span>
        <span style={{
          background: palette.bg, color: palette.fg,
          fontFamily: "var(--font-body)", fontWeight: 700,
          letterSpacing: "0.16em", textTransform: "uppercase",
          fontSize: "9px", padding: "3px 8px", whiteSpace: "nowrap",
          border: "1px solid rgba(0,0,0,0.18)",
        }}>{palette.label}</span>
      </header>

      <p style={{
        margin: 0, fontFamily: "var(--font-display)", fontStyle: "italic",
        fontSize: "1rem", color: "var(--ink-2)", lineHeight: 1.4,
      }}>{demo.blurb}</p>

      <div style={{
        display: "flex", gap: "var(--s-3)", alignItems: "baseline",
        marginTop: "var(--s-2)", flexWrap: "wrap",
      }}>
        <span style={{
          fontFamily: "var(--font-mono)", fontSize: "0.78rem",
          color: "var(--accent-gold)", fontWeight: 700,
          letterSpacing: "0.06em",
        }}>{demo.evidence} signals</span>
        <span style={{
          fontFamily: "var(--font-body)", fontSize: "var(--t-meta)",
          color: "var(--ink-4)", letterSpacing: "0.02em",
        }}>· {demo.source}</span>
      </div>

      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        marginTop: "var(--s-3)",
        borderTop: "1px solid var(--rule-soft)", paddingTop: "var(--s-3)",
      }}>
        <span style={{
          fontFamily: "var(--font-mono)", fontSize: "0.75rem",
          color: "var(--ink-3)",
        }}>{demo.cin}</span>
        <span style={{
          fontFamily: "var(--font-body)", fontWeight: 700,
          fontSize: "0.78rem", letterSpacing: "0.18em",
          textTransform: "uppercase", color: "var(--accent-gold)",
        }}>View dossier →</span>
      </div>
    </button>
  );
}

export default function Search() {
  const navigate = useNavigate();
  const [cin, setCin] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Real Tamil Nadu companies seeded from the data.gov.in bulk CSV
  // (see scripts/seed_data_gov_in.py). Hidden if the endpoint 404s or
  // returns zero rows — keeps the page useful even before the seed
  // script has been run.
  const [tnCompanies, setTnCompanies] = useState<CompanySummary[] | null>(null);
  const [tnTotal, setTnTotal] = useState<number>(0);
  const [tnLoading, setTnLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    api.companies({ state: "TN", limit: 24 })
      .then((page) => {
        if (cancelled) return;
        setTnCompanies(page.items);
        setTnTotal(page.total);
      })
      .catch(() => { if (!cancelled) setTnCompanies([]); })
      .finally(() => { if (!cancelled) setTnLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const open = (target: string) => {
    const cleaned = target.trim().toUpperCase();
    if (!CIN_REGEX.test(cleaned)) {
      setError(`That doesn't look like a valid company ID (CIN). It should be 21 characters starting with L or U — for example: U45201MH2005PTC155294.`);
      return;
    }
    setError(null);
    navigate(`/dashboard?cin=${encodeURIComponent(cleaned)}`);
  };

  return (
    <div style={{ display: "grid", gap: "var(--s-7)", maxWidth: 960 }}>
      <header style={{ borderBottom: "1px solid var(--rule)", paddingBottom: "var(--s-5)" }}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
          FINTRACE · Company Search
        </p>
        <h1 style={{
          fontFamily: "var(--font-display)",
          fontSize: "var(--t-h1)",
          fontWeight: 500,
          color: "var(--ink)",
          margin: 0,
          letterSpacing: "-0.01em",
        }}>
          Search a company
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
          Enter a company's 21-character registration number (CIN) to run a
          full fraud risk check — we analyse financial statements, tax records,
          director networks, court proceedings, and more, then show you exactly
          what we found.
        </p>
      </header>

      {/* ── Verified cases panel ─────────────────────────────────────
          The headline surface for any new user. Four real fraud cases
          the engine catches as CRITICAL/HIGH, with click-through to
          their respective dossier route. Sits ABOVE the manual CIN
          input so a judge never lands on an empty form. */}
      <section>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "var(--s-4)", gap: "var(--s-4)" }}>
          <div>
            <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>
              Verified fraud cases · click any tile
            </p>
            <p style={{
              color: "var(--ink-3)", margin: 0,
              fontFamily: "var(--font-body)", fontSize: "var(--t-meta)",
              maxWidth: "60ch", lineHeight: 1.55,
            }}>
              Each case below is anchored on real public-record data — SFIO
              investigation reports, NCLT CIRP admissions, RBI wilful
              defaulter declarations, DGGI Zonal Unit busts. Click any tile
              to run the live analysis.
            </p>
          </div>
        </header>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: "var(--s-4)",
        }}>
          {DEMO_CASES.map((demo) => (
            <VerifiedCaseTile
              key={demo.key}
              demo={demo}
              onActivate={() => navigate(demo.route)}
            />
          ))}
        </div>
      </section>

      <article style={cardStyle}>
        <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-3)", color: "var(--ink-3)" }}>
          Or analyse any CIN
        </p>
        <form
          onSubmit={(e) => { e.preventDefault(); open(cin); }}
          style={{ display: "flex", gap: "var(--s-3)", alignItems: "stretch" }}
        >
          <input
            id="cin-search"
            value={cin}
            onChange={(e) => setCin(e.target.value)}
            placeholder="U45201MH2005PTC155294"
            aria-label="CIN"
            spellCheck={false}
            autoCapitalize="characters"
            autoCorrect="off"
            style={inputStyle}
          />
          <button type="submit" style={primaryBtn}>Analyse</button>
        </form>
        {error && (
          <p style={{
            color: "var(--risk-critical)",
            marginTop: "var(--s-4)",
            marginBottom: 0,
            paddingLeft: "var(--s-3)",
            borderLeft: "2px solid var(--risk-critical)",
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-meta)",
            lineHeight: 1.5,
          }}>{error}</p>
        )}
      </article>

      {(tnLoading || (tnCompanies && tnCompanies.length > 0)) && (
        <section style={{ opacity: 0.85 }}>
          <p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)", color: "var(--ink-4)" }}>
            Newly registered · no analysis yet
          </p>
          <p style={{
            color: "var(--ink-3)",
            margin: 0,
            marginBottom: "var(--s-4)",
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-meta)",
            lineHeight: 1.55,
            maxWidth: "60ch",
          }}>
            {tnTotal.toLocaleString()} Tamil Nadu companies from the
            data.gov.in bulk MCA snapshot (showing {tnCompanies?.length ?? 0}{" "}
            newest registrations). These companies have only public master
            data on file — upload financials via <em>/upload</em> to enable
            the full fraud analysis, or run the M0 shell-cluster atlas to
            find mass-incorporation patterns across the corpus.
          </p>
          {tnLoading && (
            <p style={{
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--t-meta)",
              margin: 0,
            }}>Loading...</p>
          )}
          {tnCompanies && tnCompanies.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "var(--s-3)" }}>
              {tnCompanies.map((c) => (
                <button
                  key={c.cin}
                  type="button"
                  onClick={() => open(c.cin)}
                  style={{ ...chipBtn, padding: "var(--s-4)" }}
                >
                  <p style={{
                    fontFamily: "var(--font-body)",
                    fontSize: "var(--t-meta)",
                    color: "var(--ink)",
                    margin: 0,
                    marginBottom: "var(--s-1)",
                    fontWeight: 600,
                    lineHeight: 1.3,
                  }}>{c.name ?? c.cin}</p>
                  <p style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--t-eyebrow)",
                    letterSpacing: "0.04em",
                    color: "var(--ink-3)",
                    margin: 0,
                  }}>
                    {c.cin}
                    {c.incorporation_year != null && (
                      <span style={{ marginLeft: "var(--s-2)", color: "var(--accent-gold)" }}>
                        · {c.incorporation_year}
                      </span>
                    )}
                  </p>
                  <QualityBadge q={c.data_quality} />
                </button>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
