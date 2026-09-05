import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { BAND_PALETTE, SEVERITY_PALETTE, api, type FraudSignal } from "../lib/api";
import dhflSeed from "../../../infra/seeds/dhfl/dhfl_cluster.json";

type ClusterCompany = { cin: string; name: string; employee_count_reported?: number; registered_address?: string; };
type ClusterCharge = { cin: string; charge_id: string; lender_name: string; amount: number; creation_date: string; satisfaction_date?: string; };
type FundedRepayment = { funding_loan_id: string; funded_repayment_loan_id: string; days_between: number; amount_overlap_pct: number; };

const cluster = dhflSeed as {
  cluster_id: string;
  description: string;
  companies: ClusterCompany[];
  charges: ClusterCharge[];
  funded_repayments: FundedRepayment[];
};

const DHFL = cluster.companies[0];
const DHFL_CIN = DHFL.cin;

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
  padding: "var(--s-5)",
  borderTop: "1px solid var(--rule)",
  borderBottom: "1px solid var(--rule-soft)",
  boxShadow: "var(--shadow-card)",
};

function formatCr(value: number): string {
  return `Rs ${(value / 10_000_000).toLocaleString("en-IN", { maximumFractionDigits: 1 })} cr`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p style={{ ...eyebrow, color: "var(--ink-3)", margin: 0 }}>{label}</p><p style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-h3)", margin: "var(--s-1) 0 0", color: "var(--ink)" }}>{value}</p></div>;
}

function LoanFlow() {
  const chargesById = new Map(cluster.charges.map((charge) => [charge.charge_id, charge]));
  const companyByCin = new Map(cluster.companies.map((company) => [company.cin, company]));
  const flows = cluster.funded_repayments.map((edge) => ({ edge, funding: chargesById.get(edge.funding_loan_id), repaid: chargesById.get(edge.funded_repayment_loan_id) })).filter((flow) => flow.funding && flow.repaid) as Array<{ edge: FundedRepayment; funding: ClusterCharge; repaid: ClusterCharge }>;
  const totalFlow = flows.reduce((sum, flow) => sum + flow.repaid.amount, 0);

  return (
    <section style={panel}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-5)", flexWrap: "wrap" }}>
        <div><p style={{ ...eyebrow, margin: 0 }}>Live topology from {cluster.cluster_id}</p><h2 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h2)", fontWeight: 500, margin: "var(--s-1) 0 0" }}>FUNDED_REPAYMENT_OF loan chain</h2></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(100px, 1fr))", gap: "var(--s-4)", minWidth: 330 }}><Metric label="Companies" value={String(cluster.companies.length)} /><Metric label="Round trips" value={String(flows.length)} /><Metric label="Repaid value" value={formatCr(totalFlow)} /></div>
      </div>

      <svg viewBox="0 0 980 430" role="img" aria-label="DHFL evergreening loan flow graph" style={{ width: "100%", marginTop: "var(--s-5)" }}>
        <defs><marker id="eg-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#a07127" /></marker></defs>
        <rect x="382" y="145" width="216" height="116" fill="#f3ead2" stroke="#0e0d0a" strokeWidth="1.5" />
        <text x="490" y="178" textAnchor="middle" fontFamily="var(--font-display)" fontSize="22" fill="#0e0d0a">DHFL</text>
        <text x="490" y="202" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="11" fill="#585045">{DHFL_CIN}</text>
        <text x="490" y="228" textAnchor="middle" fontFamily="var(--font-body)" fontSize="12" fill="#585045">3 short-cycle charges</text>
        {flows.map((flow, index) => {
          const y = index === 0 ? 92 : 330;
          const shell = companyByCin.get(flow.funding.cin);
          return <g key={flow.edge.funding_loan_id}><rect x="42" y={y - 44} width="250" height="88" fill="#ddd0ab" stroke="#a07127" strokeWidth="1.5" /><text x="167" y={y - 16} textAnchor="middle" fontFamily="var(--font-display)" fontSize="16" fill="#0e0d0a">{shell?.name ?? flow.funding.cin}</text><text x="167" y={y + 6} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="10" fill="#585045">{flow.funding.charge_id}</text><text x="167" y={y + 26} textAnchor="middle" fontFamily="var(--font-body)" fontSize="11" fill="#585045">{formatCr(flow.funding.amount)} fresh loan</text><line x1="292" y1={y} x2="382" y2="203" stroke="#a07127" strokeWidth="2.2" markerEnd="url(#eg-arrow)" /><text x="330" y={(y + 203) / 2 - 8} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="11" fill="#7f1d1d">{flow.edge.amount_overlap_pct.toFixed(1)}%</text><rect x="688" y={y - 44} width="250" height="88" fill="#f3ead2" stroke="#7f1d1d" strokeWidth="1.5" /><text x="813" y={y - 16} textAnchor="middle" fontFamily="var(--font-display)" fontSize="16" fill="#0e0d0a">{flow.repaid.charge_id}</text><text x="813" y={y + 6} textAnchor="middle" fontFamily="var(--font-body)" fontSize="11" fill="#585045">repaid after {flow.edge.days_between} days</text><text x="813" y={y + 26} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="11" fill="#585045">{formatCr(flow.repaid.amount)}</text><line x1="598" y1="203" x2="688" y2={y} stroke="#7f1d1d" strokeWidth="2.2" markerEnd="url(#eg-arrow)" /></g>;
        })}
      </svg>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: "var(--s-4)", borderTop: "1px solid var(--rule-soft)", paddingTop: "var(--s-4)" }}>
        {cluster.companies.map((company) => <div key={company.cin}><p style={{ ...eyebrow, margin: 0 }}>{company.employee_count_reported ?? "-"} employees</p><h3 style={{ margin: "var(--s-1) 0", fontFamily: "var(--font-display)", fontSize: "1rem", fontWeight: 500 }}>{company.name}</h3><code style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--ink-3)" }}>{company.cin}</code></div>)}
      </div>
    </section>
  );
}

function EvidenceList({ signals }: { signals: FraudSignal[] }) {
  const graphSignals = signals.filter((signal) => signal.module_name === "m04_graph_patterns");
  const shown = graphSignals.length > 0 ? graphSignals : signals;
  return <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: "var(--s-3)" }}>{shown.slice(0, 8).map((signal) => <li key={signal.signal_id} style={{ borderLeft: `3px solid ${SEVERITY_PALETTE[signal.severity]}`, paddingLeft: "var(--s-3)" }}><p style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-eyebrow)", color: "var(--accent-gold)", letterSpacing: "0.08em", margin: 0 }}>{signal.signal_type}</p><p style={{ fontFamily: "var(--font-body)", fontSize: "var(--t-meta)", color: "var(--ink-2)", margin: "var(--s-1) 0 0", lineHeight: 1.5 }}>{signal.evidence_string}</p>{signal.triggered_by.length > 0 && <details style={{ marginTop: 6, color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: "0.72rem" }}><summary style={{ cursor: "pointer", color: "var(--accent-gold)" }}>Source records - {signal.triggered_by.length}</summary>{signal.triggered_by.map((ref, index) => <div key={index} style={{ paddingTop: 4 }}>{Object.entries(ref).map(([k, v]) => `${k}=${String(v)}`).join(" | ")}</div>)}</details>}</li>)}</ul>;
}

export default function Evergreening() {
  const query = useQuery({ queryKey: ["analyse", DHFL_CIN], queryFn: () => api.analyse(DHFL_CIN) });
  const band = query.data ? BAND_PALETTE[query.data.risk_band] : null;

  return (
    <div style={{ display: "grid", gap: "var(--s-6)", maxWidth: 1180 }}>
      <header style={{ borderBottom: "1px solid var(--rule)", paddingBottom: "var(--s-5)" }}><p style={{ ...eyebrow, margin: 0, marginBottom: "var(--s-2)" }}>Investigation - bank loan evergreening graph</p><h1 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h1)", fontWeight: 500, color: "var(--ink)", margin: 0 }}>DHFL Round-Trip Repayment Cluster</h1><div style={{ height: 1, background: "var(--accent-gold)", width: 56, margin: "var(--s-4) 0" }} aria-hidden /><p style={{ color: "var(--ink-2)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-body)", maxWidth: "78ch", lineHeight: 1.6 }}>{cluster.description}</p><div style={{ marginTop: "var(--s-3)", display: "inline-flex", gap: 6, alignItems: "center", fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "#585045" }}><span aria-hidden style={{ width: 6, height: 6, background: "#585045", borderRadius: "50%", display: "inline-block" }} />SFIO / RBI public-record pattern - graph fixture active</div></header>
      <LoanFlow />
      {query.isLoading && <section style={panel}><p style={{ ...eyebrow, margin: 0 }}>Running live DHFL analysis...</p></section>}
      {query.error && <section style={{ ...panel, borderLeft: "4px solid var(--risk-critical)" }}><p style={{ color: "var(--risk-critical)", margin: 0 }}>{(query.error as Error).message}</p></section>}
      {query.data && band && <section style={panel}><div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s-5)", flexWrap: "wrap", alignItems: "flex-start" }}><div><p style={{ ...eyebrow, margin: 0 }}>Live score for {query.data.cin}</p><h2 style={{ fontFamily: "var(--font-display)", fontSize: "var(--t-h2)", fontWeight: 500, margin: "var(--s-1) 0 0" }}>{query.data.company_name}</h2></div><span style={{ background: band.bg, color: band.fg, padding: "var(--s-2) var(--s-4)", fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.18em", textTransform: "uppercase", fontWeight: 700 }}>{query.data.risk_band}</span></div><div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--s-5)", marginTop: "var(--s-5)", paddingTop: "var(--s-4)", borderTop: "1px solid var(--rule-soft)" }}><Metric label="Fraud risk" value={query.data.fraud_risk_score.toFixed(1)} /><Metric label="Override" value={query.data.override_applied ? "Court records" : "No"} /><Metric label="Info quality" value={`${query.data.data_confidence}%`} /><Metric label="Signals" value={String(query.data.evidence_chain.length)} /></div><div style={{ marginTop: "var(--s-5)" }}><p style={{ ...eyebrow, margin: "0 0 var(--s-3)" }}>Graph-pattern evidence</p><EvidenceList signals={query.data.evidence_chain} /></div><Link to={`/graph/${query.data.cin}`} style={{ display: "inline-block", marginTop: "var(--s-5)", fontFamily: "var(--font-body)", fontSize: "var(--t-eyebrow)", letterSpacing: "0.18em", textTransform: "uppercase", fontWeight: 600, color: "var(--accent-gold)", textDecoration: "none", borderBottom: "1px solid var(--accent-gold)" }}>Open provenance graph</Link></section>}
    </div>
  );
}
