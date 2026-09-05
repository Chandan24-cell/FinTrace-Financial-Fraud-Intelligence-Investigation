import { useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AnalyseResponse, BAND_PALETTE, SEVERITY_PALETTE, api } from "../lib/api";
import ringSeed from "../../../infra/seeds/itc_carousel/ring.json";

type RingEntity = {
  gstin: string;
  name: string;
  cin?: string;
  registration_date: string;
  is_cancelled: boolean;
  is_missing_trader?: boolean;
  aggregate_turnover: number;
  tax_paid_ytd: number;
};

type RingEdge = {
  from_gstin: string;
  to_gstin: string;
  period: string;
  amount: number;
  invoice_count: number;
  risk_flag: string;
};

const ring = ringSeed as {
  ring_id: string;
  description: string;
  source_url: string;
  dggi_zone: string;
  entity_disclosure: string;
  verified_date: string;
  gst_entities: RingEntity[];
  edges: RingEdge[];
};

const eyebrow: React.CSSProperties = {
  fontFamily: "var(--font-body)",
  fontSize: "var(--t-eyebrow)",
  letterSpacing: "0.22em",
  textTransform: "uppercase",
  color: "var(--accent-gold)",
  fontWeight: 700,
};

const panel: React.CSSProperties = {
  background: "var(--paper-elevated)",
  borderTop: "1px solid var(--rule)",
  borderBottom: "1px solid var(--rule-soft)",
  boxShadow: "var(--shadow-card)",
  padding: "var(--s-5)",
};

const RETRIABLE_HTTP_CODES = new Set([408, 425, 429, 500, 502, 503, 504]);

function isRetriable(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  const match = msg.match(/-> (\d{3})/);
  return match ? RETRIABLE_HTTP_CODES.has(Number(match[1])) : false;
}

function formatCr(value: number): string {
  return `Rs ${(value / 10_000_000).toLocaleString("en-IN", { maximumFractionDigits: 1 })} cr`;
}

function shortGstin(gstin: string): string {
  return `${gstin.slice(0, 4)}...${gstin.slice(-4)}`;
}

function aggregateEdges(edges: RingEdge[]) {
  const byPair = new Map<string, { from: string; to: string; amount: number; invoices: number; periods: Set<string> }>();
  for (const edge of edges) {
    const key = `${edge.from_gstin}->${edge.to_gstin}`;
    const prev = byPair.get(key) ?? { from: edge.from_gstin, to: edge.to_gstin, amount: 0, invoices: 0, periods: new Set<string>() };
    prev.amount += edge.amount;
    prev.invoices += edge.invoice_count;
    prev.periods.add(edge.period);
    byPair.set(key, prev);
  }
  return [...byPair.values()];
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p style={{ ...eyebrow, color: "var(--ink-3)", margin: 0 }}>{label}</p>
      <p style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-h3)", margin: "var(--s-1) 0 0", color: "var(--ink)" }}>{value}</p>
    </div>
  );
}

function RingTopology({ selectedGstin, onSelect }: { selectedGstin: string; onSelect: (gstin: string) => void }) {
  const edges = useMemo(() => aggregateEdges(ring.edges), []);
  const nodeByGstin = new Map(ring.gst_entities.map((entity) => [entity.gstin, entity]));
  const cx = 390;
  const cy = 260;
  const radius = 185;
  const points = ring.gst_entities.map((entity, index) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / ring.gst_entities.length;
    return { entity, x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius };
  });
  const pointByGstin = new Map(points.map((p) => [p.entity.gstin, p]));
  const selected = nodeByGstin.get(selectedGstin) ?? ring.gst_entities[0];
  const taxRatio = selected.aggregate_turnover > 0 ? (selected.tax_paid_ytd / selected.aggregate_turnover) * 100 : 0;

  return (
    <section style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-5)", flexWrap: "wrap" }}>
        <div>
          <p style={{ ...eyebrow, margin: 0 }}>Live topology from {ring.ring_id}</p>
          <h2 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h2)", fontWeight: 500, margin: "var(--s-1) 0 0" }}>{ring.gst_entities.length}-node CLAIMS_ITC_FROM cycle</h2>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(90px, 1fr))", gap: "var(--s-4)", minWidth: 300 }}>
          <Metric label="Cycle edges" value={String(edges.length)} />
          <Metric label="Invoices" value={String(edges.reduce((sum, edge) => sum + edge.invoices, 0))} />
          <Metric label="ITC churn" value={formatCr(edges.reduce((sum, edge) => sum + edge.amount, 0))} />
        </div>
      </div>

      <svg viewBox="0 0 780 540" role="img" aria-label="Seven-node GST input-tax-credit carousel graph" style={{ width: "100%", marginTop: "var(--s-4)" }}>
        <defs><marker id="itc-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#a07127" /></marker></defs>
        {edges.map((edge) => {
          const source = pointByGstin.get(edge.from);
          const target = pointByGstin.get(edge.to);
          if (!source || !target) return null;
          const active = selectedGstin === edge.from || selectedGstin === edge.to;
          return <g key={`${edge.from}-${edge.to}`}><line x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke={active ? "#7f1d1d" : "#a07127"} strokeWidth={active ? 3 : 1.5} markerEnd="url(#itc-arrow)" opacity={active ? 1 : 0.68} /><text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 8} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="11" fill="#585045">{formatCr(edge.amount)}</text></g>;
        })}
        {points.map(({ entity, x, y }, index) => {
          const selectedNode = selectedGstin === entity.gstin;
          const risky = entity.is_cancelled || entity.is_missing_trader;
          return <g key={entity.gstin} onClick={() => onSelect(entity.gstin)} style={{ cursor: "pointer" }}><circle cx={x} cy={y} r={selectedNode ? 42 : 36} fill={risky ? "#7f1d1d" : "#f3ead2"} stroke={selectedNode ? "#0e0d0a" : "#a07127"} strokeWidth={selectedNode ? 3 : 1.5} /><text x={x} y={y - 8} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="13" fill={risky ? "white" : "#0e0d0a"}>G{index + 1}</text><text x={x} y={y + 9} textAnchor="middle" fontFamily="var(--font-body)" fontSize="10" fill={risky ? "white" : "#585045"}>{shortGstin(entity.gstin)}</text>{entity.is_cancelled && <text x={x} y={y + 24} textAnchor="middle" fontFamily="var(--font-body)" fontSize="9" fill="white">cancelled</text>}{entity.is_missing_trader && !entity.is_cancelled && <text x={x} y={y + 24} textAnchor="middle" fontFamily="var(--font-body)" fontSize="9" fill="white">missing</text>}</g>;
        })}
      </svg>

      <div style={{ display: "grid", gridTemplateColumns: "1.25fr repeat(3, 1fr)", gap: "var(--s-4)", borderTop: "1px solid var(--rule-soft)", paddingTop: "var(--s-4)" }}>
        <div><p style={{ ...eyebrow, margin: 0 }}>Selected GST entity</p><h3 style={{ margin: "var(--s-1) 0", fontFamily: "var(--font-display)", fontSize: "var(--t-h3)", fontWeight: 500 }}>{selected.name}</h3><code style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-meta)", color: "var(--ink-3)" }}>{selected.gstin}</code></div>
        <Metric label="Turnover" value={formatCr(selected.aggregate_turnover)} />
        <Metric label="Tax paid" value={formatCr(selected.tax_paid_ytd)} />
        <Metric label="Tax / turnover" value={`${taxRatio.toFixed(2)}%`} />
      </div>
    </section>
  );
}

function AnalysisCard({ entity, data, isLoading, isFetching, failureCount, error }: { entity: RingEntity; data?: AnalyseResponse; isLoading: boolean; isFetching: boolean; failureCount: number; error: unknown; }) {
  const retrying = isFetching && failureCount > 0 && !data;
  const band = data ? BAND_PALETTE[data.risk_band] : null;
  const graphSignals = data?.evidence_chain.filter((signal) => signal.module_name === "m04_graph_patterns") ?? [];
  return (
    <article style={{ ...panel, padding: "var(--s-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-3)", alignItems: "flex-start" }}><div><p style={{ ...eyebrow, margin: 0 }}>{shortGstin(entity.gstin)}</p><h3 style={{ margin: "var(--s-1) 0", fontFamily: "var(--font-display)", fontSize: "1.15rem", fontWeight: 500 }}>{entity.name}</h3><code style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--ink-3)" }}>{entity.cin}</code></div>{band && <span style={{ background: band.bg, color: band.fg, padding: "6px 9px", fontSize: "0.65rem", letterSpacing: "0.12em", fontWeight: 700 }}>{data!.risk_band}</span>}</div>
      {(isLoading || retrying) && <p style={{ color: "var(--ink-4)", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)" }}>{retrying ? `Retrying live analysis (${failureCount + 1}/4)` : "Running live analysis..."}</p>}
      {Boolean(error) && !isLoading && !retrying && <p style={{ color: "var(--risk-critical)", fontFamily: "var(--font-body)", fontSize: "var(--t-meta)" }}>{(error as Error).message}</p>}
      {data && <><div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "var(--s-3)", marginTop: "var(--s-4)", borderTop: "1px solid var(--rule-soft)", paddingTop: "var(--s-3)" }}><Metric label="Score" value={data.fraud_risk_score.toFixed(1)} /><Metric label="DC" value={`${data.data_confidence}%`} /><Metric label="Signals" value={String(data.evidence_chain.length)} /></div>{graphSignals.length > 0 && <ul style={{ margin: "var(--s-3) 0 0", padding: 0, listStyle: "none", display: "grid", gap: 8 }}>{graphSignals.slice(0, 2).map((signal) => <li key={signal.signal_id} style={{ borderLeft: `3px solid ${SEVERITY_PALETTE[signal.severity]}`, paddingLeft: 10 }}><p style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--accent-gold)" }}>{signal.signal_type}</p><p style={{ margin: "2px 0 0", fontFamily: "var(--font-body)", fontSize: "0.82rem", color: "var(--ink-2)", lineHeight: 1.45 }}>{signal.evidence_string}</p></li>)}</ul>}<Link to={`/graph/${data.cin}`} style={{ display: "inline-block", marginTop: "var(--s-4)", color: "var(--accent-gold)", fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", textTransform: "uppercase", letterSpacing: "0.16em", textDecoration: "none", borderBottom: "1px solid var(--accent-gold)" }}>Open provenance graph</Link></>}
    </article>
  );
}

export default function ITCCarousel() {
  const cins = ring.gst_entities.map((entity) => entity.cin).filter((cin): cin is string => Boolean(cin));
  const [selectedGstin, setSelectedGstin] = useState(ring.gst_entities[0].gstin);
  const results = useQueries({ queries: cins.map((cin) => ({ queryKey: ["analyse", cin], queryFn: () => api.analyse(cin), retry: (failureCount: number, error: unknown) => failureCount < 3 && isRetriable(error), retryDelay: (attempt: number) => Math.min(300 * 3 ** attempt, 2700) })) });
  const resultByCin = new Map(cins.map((cin, index) => [cin, results[index]]));
  return (
    <div style={{ display: "grid", gap: "var(--s-6)", maxWidth: 1180 }}>
      <header style={{ borderBottom: "1px solid var(--rule)", paddingBottom: "var(--s-5)" }}><p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>Investigation - GST tax-credit graph</p><h1 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h1)", fontWeight: 500, color: "var(--ink)", margin: 0 }}>DGGI Mumbai ITC Carousel</h1><div style={{ height: 1, background: "var(--accent-gold)", width: 56, margin: "var(--s-4) 0" }} aria-hidden /><p style={{ color: "var(--ink-2)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-body)", maxWidth: "78ch", lineHeight: 1.6 }}>{ring.description}</p><div style={{ marginTop: "var(--s-3)", display: "flex", gap: "var(--s-3)", flexWrap: "wrap", alignItems: "center", fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "#a16207" }}><span>DGGI {ring.dggi_zone}</span><span>Verified {ring.verified_date}</span><a href={ring.source_url} style={{ color: "#a16207" }}>CBIC press-release archive</a></div></header>
      <RingTopology selectedGstin={selectedGstin} onSelect={setSelectedGstin} />
      <section style={{ display: "grid", gap: "var(--s-4)" }}><div><p style={{ ...eyebrow, margin: 0 }}>Live per-node analysis</p><h2 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h2)", fontWeight: 500, margin: "var(--s-1) 0 0" }}>Every CIN in the ring is scored independently</h2></div><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(310px, 1fr))", gap: "var(--s-4)" }}>{ring.gst_entities.map((entity) => { const result = entity.cin ? resultByCin.get(entity.cin) : undefined; return <AnalysisCard key={entity.gstin} entity={entity} data={result?.data} isLoading={result?.isLoading ?? false} isFetching={result?.isFetching ?? false} failureCount={result?.failureCount ?? 0} error={result?.error} />; })}</div></section>
      <section style={{ ...panel, color: "var(--ink-2)", fontFamily: "var(--font-body)", lineHeight: 1.6 }}><p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>Source disclosure</p><p style={{ margin: 0 }}>{ring.entity_disclosure}</p></section>
    </div>
  );
}
