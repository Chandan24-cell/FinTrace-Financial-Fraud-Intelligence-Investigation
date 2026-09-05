import { useEffect, useState } from "react";
import { api, type SourceInventory, type SourceInventoryItem } from "../lib/api";

const TYPE_PALETTE: Record<string, { bg: string; label: string }> = {
  government_bulk:      { bg: "#15803d", label: "GOVT · BULK" },
  government_scraper:   { bg: "#a16207", label: "GOVT · SCRAPER" },
  court_record:         { bg: "#1d4ed8", label: "COURT" },
  industry_benchmark:   { bg: "#6b21a8", label: "BENCHMARK" },
};

const STATUS_PALETTE: Record<string, { fg: string; label: string }> = {
  live:     { fg: "#15803d", label: "LIVE" },
  stale:    { fg: "#a16207", label: "STALE" },
  missing:  { fg: "#7f1d1d", label: "MISSING" },
  external: { fg: "#6b6b6b", label: "EXTERNAL" },
};

function TypeBadge({ t }: { t: string }) {
  const p = TYPE_PALETTE[t] ?? { bg: "#6b6b6b", label: t.toUpperCase() };
  return (
    <span style={{
      display: "inline-block",
      background: p.bg, color: "#ffffff",
      fontFamily: "var(--font-body)", fontWeight: 700,
      letterSpacing: "0.14em", textTransform: "uppercase",
      fontSize: "9px", padding: "3px 8px",
    }}>{p.label}</span>
  );
}

function StatusPill({ s }: { s: string }) {
  const p = STATUS_PALETTE[s] ?? { fg: "#6b6b6b", label: s.toUpperCase() };
  return (
    <span style={{
      fontFamily: "var(--font-mono)", color: p.fg,
      fontSize: "0.78rem", fontWeight: 700, letterSpacing: "0.08em",
    }}>● {p.label}</span>
  );
}

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const days = Math.floor((Date.now() - t) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 31) return `${days} days ago`;
  const months = Math.floor(days / 30);
  return months === 1 ? "1 month ago" : `${months} months ago`;
}

function SourceRow({ s }: { s: SourceInventoryItem }) {
  return (
    <article style={{
      background: "var(--paper-elevated)",
      boxShadow: "var(--shadow-card)",
      padding: "var(--s-5) var(--s-6)",
      display: "grid", gap: "var(--s-3)",
      borderLeft: `4px solid ${TYPE_PALETTE[s.source_type]?.bg ?? "#6b6b6b"}`,
    }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-3)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-3)", flexWrap: "wrap" }}>
          <h3 style={{
            margin: 0, fontFamily: "var(--font-display)", fontWeight: 500,
            fontSize: "1.3rem", color: "var(--ink)",
          }}>{s.name}</h3>
          <TypeBadge t={s.source_type} />
        </div>
        <StatusPill s={s.status} />
      </header>

      <p style={{
        margin: 0, fontSize: "0.95rem", color: "var(--ink-2)",
        lineHeight: 1.55, maxWidth: "72ch",
      }}>{s.description}</p>

      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: "var(--s-4)", marginTop: "var(--s-2)",
        fontSize: "var(--t-meta)", color: "var(--ink-3)",
      }}>
        <div>
          <span style={{ fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ink-4)", fontWeight: 600 }}>License</span>
          <p style={{ margin: "4px 0 0", fontSize: "0.85rem" }}>{s.license}</p>
        </div>
        <div>
          <span style={{ fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ink-4)", fontWeight: 600 }}>Records on file</span>
          <p style={{ margin: "4px 0 0", fontFamily: "var(--font-mono)", fontSize: "1rem", color: "var(--ink)" }}>
            {s.record_count !== null ? s.record_count.toLocaleString() : "—"}
          </p>
        </div>
        <div>
          <span style={{ fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ink-4)", fontWeight: 600 }}>Last refreshed</span>
          <p style={{ margin: "4px 0 0", fontSize: "0.85rem" }}>{timeAgo(s.last_refreshed)}</p>
        </div>
      </div>

      <a href={s.url} target="_blank" rel="noopener noreferrer" style={{
        fontFamily: "var(--font-body)", fontSize: "0.78rem",
        fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase",
        color: "var(--accent-gold)", textDecoration: "none",
        borderBottom: "1px solid var(--accent-gold)", paddingBottom: 1,
        alignSelf: "start", marginTop: "var(--s-2)",
      }}>View source ↗</a>
    </article>
  );
}

export default function Sources() {
  const [data, setData] = useState<SourceInventory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.sources()
      .then(setData)
      .catch((e) => setError((e as Error).message));
  }, []);

  // Compute the freshest mtime across all live sources for the sticky
  // header. "Last refresh" gives judges a one-glance freshness signal
  // without having to scan every row.
  const freshest = data?.sources
    .map((s) => s.last_refreshed)
    .filter((v): v is string => Boolean(v))
    .sort()
    .at(-1) ?? null;

  return (
    <article style={{ display: "grid", gap: "var(--s-7)" }}>
      {data && (
        <div
          style={{
            position: "sticky",
            top: "var(--s-3)",
            zIndex: 5,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "var(--s-4)",
            padding: "var(--s-3) var(--s-5)",
            background: "var(--paper)",
            border: "1px solid var(--rule)",
            borderLeft: "4px solid var(--accent-gold)",
            boxShadow: "var(--shadow-card)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--t-meta)",
            color: "var(--ink-2)",
            flexWrap: "wrap",
          }}
        >
          <span>
            <strong style={{ color: "var(--ink)" }}>{data.total_sources}</strong> sources ·{" "}
            <strong style={{ color: "var(--ink)" }}>{data.total_records.toLocaleString()}</strong> records on file
          </span>
          {freshest && (
            <span style={{ color: "var(--ink-3)" }}>
              freshest refresh: {timeAgo(freshest)}
            </span>
          )}
          <span style={{ color: "var(--ink-4)" }}>
            inventory generated {data.generated_at.slice(11, 19)} UTC
          </span>
        </div>
      )}
      <header style={{ display: "grid", gap: "var(--s-3)" }}>
        <span style={{
          fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.28em", textTransform: "uppercase",
          color: "var(--accent-gold)", fontWeight: 700,
        }}>FINTRACE · Data Lineage</span>
        <h1 style={{
          fontFamily: "var(--font-display)", fontWeight: 500,
          fontSize: "var(--t-h1)", color: "var(--ink)",
          letterSpacing: "-0.01em", margin: 0,
        }}>Every signal, traced to its source.</h1>
        <div style={{ height: 1, background: "var(--accent-gold)", width: 56, margin: "var(--s-2) 0" }} aria-hidden />
        <p style={{
          fontFamily: "var(--font-display)", fontStyle: "italic",
          fontSize: "1.15rem", color: "var(--ink-2)", maxWidth: "62ch",
          margin: 0, lineHeight: 1.55,
        }}>
          The FinTrace engine fires only on public-record data — RBI, SFIO,
          CBI, NCLT, DGGI, CERSAI, MCA. Every fraud signal carries the
          government source URL that produced it. Below is the live
          inventory: each source's record count and refresh timestamp are
          read from disk at request time, so what you see here is what the
          analysis actually used.
        </p>
        {data && (
          <p style={{
            fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)",
            color: "var(--ink-3)", margin: "var(--s-3) 0 0",
          }}>
            {data.total_sources} sources · {data.total_records.toLocaleString()} records on file ·
            inventory generated at {data.generated_at}
          </p>
        )}
      </header>

      {error && (
        <p role="alert" style={{
          color: "var(--risk-critical)", padding: "var(--s-3) var(--s-4)",
          borderLeft: "2px solid var(--risk-critical)",
        }}>Failed to load sources: {error}</p>
      )}

      {data && (
        <div style={{ display: "grid", gap: "var(--s-5)" }}>
          {data.sources.map((s) => <SourceRow key={s.id} s={s} />)}
        </div>
      )}

      {!data && !error && (
        <p style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>Loading data inventory…</p>
      )}
    </article>
  );
}
