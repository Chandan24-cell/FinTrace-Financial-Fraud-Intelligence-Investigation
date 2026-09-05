import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface ShellClusterOut {
  cluster_id: string;
  signal_type: string;
  size: number;
  state: string | null;
  anchor_value: string;
  sample_cins: string[];
  sample_names: string[];
  evidence_string: string;
  source: string;
}

interface ShellsPage {
  signal: string;
  total: number;
  limit: number;
  offset: number;
  clusters: ShellClusterOut[];
  atlas: {
    rows_scanned: number;
    csv_path: string | null;
    built_at: string | null;
    address_clusters: number;
    mass_incorporation_clusters: number;
    paper_shells: number;
    flagged_cins: number;
  };
}

type SignalFilter = "address" | "mass_incorp" | "paper";

const TAB_LABELS: Record<SignalFilter, { label: string; eyebrow: string; blurb: string; color: string }> = {
  address: {
    label: "Address clusters",
    eyebrow: "Shared registered office",
    blurb:
      "N companies share one registered address. Real businesses rarely co-locate at scale — a documented shell-firm tell.",
    color: "#7f1d1d",
  },
  mass_incorp: {
    label: "Mass incorporation",
    eyebrow: "Same-day batch",
    blurb:
      "N companies registered in the same state on the same day. Coordinated batch registrations are a known shell-network pattern (DGGI / SFIO enforcement, 2020-2024).",
    color: "#b45309",
  },
  paper: {
    label: "Paper shells",
    eyebrow: "Paid-up < 10% authorised",
    blurb:
      "Companies where paid-up capital is under 10% of authorised AND > 2 years old. Authorised on paper, never operationally funded.",
    color: "#a16207",
  },
};

const SIGNAL_BADGE_COLORS: Record<string, string> = {
  ADDRESS_CLUSTER: "#7f1d1d",
  MASS_INCORPORATION: "#b45309",
  PAPER_SHELL: "#a16207",
};

async function fetchShells(signal: SignalFilter, limit = 20): Promise<ShellsPage> {
  const res = await fetch(`/api/shells?signal=${signal}&limit=${limit}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`Shells API ${res.status}`);
  return res.json();
}

function ClusterCard({ c, onCinClick }: { c: ShellClusterOut; onCinClick: (cin: string) => void }) {
  const badgeColor = SIGNAL_BADGE_COLORS[c.signal_type] ?? "#6b6b6b";
  return (
    <article
      style={{
        background: "var(--paper-elevated)",
        boxShadow: "var(--shadow-card)",
        padding: "var(--s-5) var(--s-6)",
        borderLeft: `4px solid ${badgeColor}`,
        display: "grid",
        gap: "var(--s-3)",
      }}
    >
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-3)", flexWrap: "wrap" }}>
        <div>
          <p style={{
            fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)",
            letterSpacing: "0.18em", textTransform: "uppercase",
            color: "var(--ink-4)", fontWeight: 600, margin: 0,
            marginBottom: 4,
          }}>
            {c.cluster_id} {c.state ? `· ${c.state}` : ""}
          </p>
          <h3 style={{
            margin: 0, fontFamily: "var(--font-display)", fontWeight: 500,
            fontSize: "1.3rem", color: "var(--ink)", lineHeight: 1.2,
          }}>{c.anchor_value}</h3>
        </div>
        <span style={{
          background: badgeColor, color: "white",
          fontFamily: "var(--font-mono)", fontWeight: 700,
          fontSize: "0.85rem", padding: "4px 10px",
          letterSpacing: "0.04em",
        }}>{c.size} companies</span>
      </header>

      <p style={{
        margin: 0, fontSize: "0.95rem", color: "var(--ink-2)",
        lineHeight: 1.55, maxWidth: "72ch",
      }}>{c.evidence_string}</p>

      <div style={{ display: "grid", gap: 6 }}>
        <p style={{
          fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.16em", textTransform: "uppercase",
          color: "var(--ink-4)", fontWeight: 600, margin: 0,
        }}>Sample members · click any CIN to analyse</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {c.sample_cins.map((cin, i) => (
            <button
              key={cin}
              type="button"
              onClick={() => onCinClick(cin)}
              title={c.sample_names[i] ?? cin}
              style={{
                fontFamily: "var(--font-mono)", fontSize: "0.78rem",
                padding: "5px 10px",
                background: "var(--paper)",
                border: "1px solid var(--rule-soft)",
                cursor: "pointer", letterSpacing: "0.02em",
                color: "var(--ink-2)", borderRadius: 0,
              }}
            >
              {cin}
            </button>
          ))}
        </div>
      </div>

      <p style={{
        margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)",
        color: "var(--ink-4)", fontStyle: "italic",
      }}>{c.source}</p>
    </article>
  );
}

function AtlasHeader({ atlas, activeTab }: { atlas: ShellsPage["atlas"]; activeTab: SignalFilter }) {
  const counts = [
    { label: "rows scanned", value: atlas.rows_scanned.toLocaleString() },
    { label: "address clusters", value: atlas.address_clusters.toLocaleString() },
    { label: "mass-incorp events", value: atlas.mass_incorporation_clusters.toLocaleString() },
    { label: "paper shells", value: atlas.paper_shells.toLocaleString() },
    { label: "flagged CINs", value: atlas.flagged_cins.toLocaleString() },
  ];
  const tab = TAB_LABELS[activeTab];
  return (
    <header style={{ display: "grid", gap: "var(--s-4)" }}>
      <div>
        <p style={{
          fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.28em", textTransform: "uppercase",
          color: "var(--accent-gold)", fontWeight: 700, margin: 0,
          marginBottom: "var(--s-2)",
        }}>FINTRACE · M0 Shell Atlas</p>
        <h1 style={{
          fontFamily: "var(--font-display)", fontWeight: 500,
          fontSize: "var(--t-h1)", color: "var(--ink)",
          letterSpacing: "-0.01em", margin: 0,
        }}>{tab.eyebrow} — ranked clusters.</h1>
        <div style={{ height: 1, background: "var(--accent-gold)", width: 56, margin: "var(--s-3) 0" }} aria-hidden />
        <p style={{
          fontFamily: "var(--font-display)", fontStyle: "italic",
          fontSize: "1.1rem", color: "var(--ink-2)", maxWidth: "62ch",
          margin: 0, lineHeight: 1.55,
        }}>{tab.blurb}</p>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
        gap: "var(--s-3)",
        background: "var(--paper-elevated)",
        padding: "var(--s-4) var(--s-5)",
        boxShadow: "var(--shadow-card)",
      }}>
        {counts.map((c) => (
          <div key={c.label}>
            <p style={{
              fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)",
              letterSpacing: "0.16em", textTransform: "uppercase",
              color: "var(--ink-4)", fontWeight: 600, margin: 0,
            }}>{c.label}</p>
            <p style={{
              fontFamily: "var(--font-mono)", fontSize: "1.1rem",
              color: "var(--ink)", margin: "4px 0 0",
            }}>{c.value}</p>
          </div>
        ))}
      </div>
      {atlas.built_at && (
        <p style={{
          margin: 0, fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)",
          color: "var(--ink-3)",
        }}>
          Atlas built at {atlas.built_at} · csv: {atlas.csv_path ?? "n/a"}
        </p>
      )}
    </header>
  );
}

function TabBar({ active, onChange }: { active: SignalFilter; onChange: (t: SignalFilter) => void }) {
  const tabs: SignalFilter[] = ["address", "mass_incorp", "paper"];
  return (
    <div style={{
      display: "flex", gap: 0,
      borderBottom: "1px solid var(--rule)",
    }}>
      {tabs.map((t) => {
        const isActive = active === t;
        return (
          <button
            key={t}
            type="button"
            onClick={() => onChange(t)}
            style={{
              padding: "var(--s-3) var(--s-5)",
              background: "transparent",
              color: isActive ? "var(--ink)" : "var(--ink-3)",
              border: 0,
              borderBottom: isActive ? "2px solid var(--accent-gold)" : "2px solid transparent",
              fontFamily: "var(--font-body)",
              fontSize: "0.85rem",
              fontWeight: isActive ? 700 : 500,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              cursor: "pointer",
              transition: "color 120ms",
            }}
          >
            {TAB_LABELS[t].label}
          </button>
        );
      })}
    </div>
  );
}

export default function ShellAtlas() {
  const navigate = useNavigate();
  const [active, setActive] = useState<SignalFilter>("address");
  const [data, setData] = useState<ShellsPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchShells(active, 20)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError((e as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [active]);

  const onCinClick = (cin: string) => {
    navigate(`/dashboard?cin=${encodeURIComponent(cin)}`);
  };

  return (
    <article style={{ display: "grid", gap: "var(--s-6)" }}>
      {data && <AtlasHeader atlas={data.atlas} activeTab={active} />}
      <TabBar active={active} onChange={setActive} />

      {loading && (
        <p style={{
          fontFamily: "var(--font-mono)", color: "var(--ink-3)",
          padding: "var(--s-4) 0",
        }}>
          Loading atlas… first call builds the index from the 250k-row CSV
          (~10 seconds), subsequent calls hit memory.
        </p>
      )}

      {error && (
        <p role="alert" style={{
          color: "var(--risk-critical)", padding: "var(--s-3) var(--s-4)",
          borderLeft: "2px solid var(--risk-critical)",
        }}>
          Atlas request failed: {error}
        </p>
      )}

      {data && data.clusters.length === 0 && !loading && (
        <p style={{
          fontFamily: "var(--font-display)", fontStyle: "italic",
          color: "var(--ink-3)", padding: "var(--s-5) 0",
          fontSize: "1.05rem",
        }}>
          No clusters at this threshold yet. The atlas seeds itself from
          the data.gov.in TN bulk CSV; if you're on a fresh deploy the
          CSV may not be mounted yet.
        </p>
      )}

      {data && data.clusters.length > 0 && (
        <div style={{ display: "grid", gap: "var(--s-5)" }}>
          {data.clusters.map((c) => (
            <ClusterCard key={c.cluster_id} c={c} onCinClick={onCinClick} />
          ))}
        </div>
      )}
    </article>
  );
}
