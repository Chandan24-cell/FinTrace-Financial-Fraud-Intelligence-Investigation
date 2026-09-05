import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationNodeDatum,
} from "d3-force";
import { BAND_PALETTE, SEVERITY_PALETTE, api } from "../lib/api";
import { DEMO_CASES } from "../lib/demoCases";

interface GraphNode extends SimulationNodeDatum {
  id: string;
  label: string;
  kind: "company" | "signal" | "ref";
  fill: string;
  // Extra metadata surfaced in the inspector panel.
  meta: Record<string, string>;
  // For signal nodes — copied straight from the provenance payload.
  signalType?: string;
  module?: string;
  severity?: string;
  scoreContribution?: number;
  evidenceString?: string;
}

interface GraphLink {
  source: string;
  target: string;
  label: string;
}

const WIDTH = 940;
const HEIGHT = 600;

// SVG presentation attributes (`fill`, `stroke`, etc.) don't resolve
// `var(--token)` — only CSS `style` properties do. Mirror the relevant
// tokens here for attribute consumers; keep in sync with tokens.css.
const HEX = {
  paper: "#f3ead2",
  paperDeep: "#ddd0ab",
  ink: "#0e0d0a",
  ink3: "#585045",
  rule: "#1a1a1a",
  ruleSoft: "#c6b890",
  accentGold: "#a07127",
  refBlue: "#1e3a8a",
} as const;

function Eyebrow({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <span className="eyebrow" style={style}>
      {children}
    </span>
  );
}

export default function GraphExplorer() {
  const { cin: cinParam } = useParams<{ cin?: string }>();
  const [searchParams] = useSearchParams();
  // No demo fallback — the page accepts a CIN via the /graph/:cin route
  // param or the CIN input below. Empty state until the analyst types one.
  const [cin, setCin] = useState(cinParam ?? "");
  useEffect(() => {
    if (cinParam) setCin(cinParam);
  }, [cinParam]);

  const query = useQuery({
    queryKey: ["provenance", cin],
    queryFn: () => api.provenance(cin),
    enabled: Boolean(cin),
  });

  const { nodes, links } = useMemo(() => {
    if (!query.data) return { nodes: [] as GraphNode[], links: [] as GraphLink[] };

    const cinNodeId = `cin:${query.data.cin}`;
    const ns: GraphNode[] = [
      {
        id: cinNodeId,
        label: query.data.cin,
        kind: "company",
        fill: HEX.ink,
        meta: {
          CIN: query.data.cin,
          Signals: String(query.data.signal_count),
          References: String(query.data.triggered_by.length),
        },
      },
    ];
    const ls: GraphLink[] = [];

    for (const s of query.data.signals) {
      ns.push({
        id: `sig:${s.signal_id}`,
        label: s.signal_type,
        kind: "signal",
        fill: SEVERITY_PALETTE[s.severity],
        meta: {
          Severity: s.severity,
          Module: s.module_name,
          Type: s.signal_type,
          Contribution: `+${s.score_contribution.toFixed(1)}`,
        },
        signalType: s.signal_type,
        module: s.module_name,
        severity: s.severity,
        scoreContribution: s.score_contribution,
        evidenceString: s.evidence_string,
      });
      ls.push({
        source: cinNodeId,
        target: `sig:${s.signal_id}`,
        label: "HAS_FRAUD_SIGNAL",
      });
    }
    for (const edge of query.data.triggered_by) {
      const refId = `ref:${edge.label ?? "node"}:${JSON.stringify(edge.ref)}`;
      if (!ns.find((n) => n.id === refId)) {
        const refMeta: Record<string, string> = {};
        for (const [k, v] of Object.entries(edge.ref)) {
          refMeta[k] = String(v);
        }
        ns.push({
          id: refId,
          label: `${edge.label ?? "ref"} · ${Object.values(edge.ref).join(" / ")}`,
          kind: "ref",
          fill: HEX.refBlue,
          meta: { Label: edge.label ?? "(unlabelled)", ...refMeta },
        });
      }
      ls.push({
        source: `sig:${edge.signal_id}`,
        target: refId,
        label: "TRIGGERED_BY",
      });
    }
    return { nodes: ns, links: ls };
  }, [query.data]);

  // Simulation + tick-driven render. We keep a ref for the latest node array
  // so the SVG redraw doesn't allocate new objects per tick (smoother on
  // projected screens that prefer steady frame timings).
  const [, setTick] = useState(0);
  useEffect(() => {
    if (nodes.length === 0) return;
    const sim = forceSimulation(nodes)
      .force(
        "link",
        forceLink<GraphNode, GraphLink>(links)
          .id((d) => d.id)
          .distance((d) => {
            const t = d.target as unknown;
            return typeof t === "object" && t !== null && (t as GraphNode).kind === "ref"
              ? 80
              : 130;
          })
          .strength(0.85),
      )
      .force("charge", forceManyBody().strength(-340))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force(
        "collide",
        forceCollide<GraphNode>((d) => (d.kind === "company" ? 38 : 22)),
      );
    // Throttle React re-renders to one per animation frame (~60 fps).
    // d3-force fires ticks faster than the browser can repaint, and our SVG
    // tree is hundreds of JSX nodes wide — without rAF gating, large
    // provenance graphs (~100+ nodes) freeze the main thread. Per
    // LOCAL_TEST_REPORT §4.5.
    let pending = false;
    sim.on("tick", () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        setTick((t) => t + 1);
      });
    });
    return () => {
      sim.stop();
    };
  }, [nodes, links]);

  // Zoom-to-fit — derived from the live node positions. We recompute every
  // render rather than memoising on `nodes.length`: x/y are populated by
  // the simulation post-mount and only known after the first tick. The
  // tick-driven re-render rate is bounded by d3's alphaDecay, so the camera
  // settles naturally without jitter at the ~20-node demo scale.
  const xs = nodes.map((n) => n.x).filter((v): v is number => typeof v === "number");
  const ys = nodes.map((n) => n.y).filter((v): v is number => typeof v === "number");
  const viewBox = (() => {
    if (xs.length === 0 || ys.length === 0) return `0 0 ${WIDTH} ${HEIGHT}`;
    const minX = Math.min(...xs) - 60;
    const minY = Math.min(...ys) - 50;
    const maxX = Math.max(...xs) + 60;
    const maxY = Math.max(...ys) + 50;
    const w = Math.max(WIDTH, maxX - minX);
    const h = Math.max(HEIGHT, maxY - minY);
    return `${minX} ${minY} ${w} ${h}`;
  })();
  const vbParts = viewBox.split(" ");

  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Auto-select a signal when the URL contains ?signal=<signal_id>
  // (set by the "Highlight in graph →" links on the Dashboard).
  const signalParam = searchParams.get("signal");
  useEffect(() => {
    if (signalParam && nodes.length > 0) {
      const targetId = `sig:${signalParam}`;
      if (nodes.find((n) => n.id === targetId)) {
        setSelectedId(targetId);
      }
    }
  }, [signalParam, nodes.length]);

  // When a signal node is selected, compute the set of nodes that form its
  // evidence subgraph: the signal itself + all ref nodes it points to via
  // TRIGGERED_BY edges. This set drives the gold-highlight rendering.
  const highlightedIds = useMemo((): Set<string> => {
    if (!selectedId) return new Set();
    const s = new Set<string>([selectedId]);
    for (const lnk of links) {
      const srcId =
        typeof lnk.source === "string"
          ? lnk.source
          : (lnk.source as unknown as GraphNode).id;
      const tgtId =
        typeof lnk.target === "string"
          ? lnk.target
          : (lnk.target as unknown as GraphNode).id;
      if (selectedId.startsWith("sig:") && srcId === selectedId && lnk.label === "TRIGGERED_BY") {
        s.add(tgtId);
      }
      if (selectedId.startsWith("ref:") && tgtId === selectedId && lnk.label === "TRIGGERED_BY") {
        s.add(srcId);
      }
    }
    return s;
  }, [selectedId, links]);

  const inspected = useMemo(
    () => nodes.find((n) => n.id === (selectedId ?? hoveredId)) ?? null,
    [nodes, selectedId, hoveredId],
  );

  return (
    <article style={{ display: "grid", gap: "var(--s-5)" }}>
      <header style={{ display: "grid", gap: "var(--s-3)" }}>
        <Eyebrow>FINTRACE · Evidence Graph</Eyebrow>
        <h1>The Evidence Graph.</h1>
        <p
          style={{
            fontFamily: "var(--font-display)",
            fontStyle: "italic",
            fontSize: "1.15rem",
            color: "var(--ink-3)",
            maxWidth: "62ch",
          }}
        >
          Every warning sign is connected to the exact record that triggered it —
          financial statements, tax filings, charge documents, or GST data.
          Click any node to inspect it. Click a warning-sign node to highlight
          its full evidence trail in gold.
        </p>
        <div className="rule-gold" aria-hidden />
      </header>

      <form
        onSubmit={(e) => e.preventDefault()}
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto",
          gap: "var(--s-3) var(--s-4)",
          padding: "var(--s-5) var(--s-6)",
          background: "var(--paper-elevated)",
          boxShadow: "var(--shadow-card)",
        }}
      >
        <div style={{ display: "grid", gap: "var(--s-2)" }}>
          <label htmlFor="graph-cin">
            <Eyebrow>Company ID (CIN)</Eyebrow>
          </label>
          <input
            id="graph-cin"
            value={cin}
            onChange={(e) => setCin(e.target.value.trim().toUpperCase())}
            spellCheck={false}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "1.1rem",
              padding: "var(--s-3) 0",
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--rule)",
              color: "var(--ink)",
              width: "100%",
              outline: "none",
            }}
          />
        </div>
      </form>

      {/* Empty state — when no CIN is typed yet, show demo-case chips so
          the analyst immediately understands what the graph view does
          without having to leave the page. Disappears once a query
          starts (loading) or returns data. */}
      {!cin && !query.isLoading && !query.data && (
        <section
          style={{
            background: "var(--paper-elevated)",
            boxShadow: "var(--shadow-card)",
            padding: "var(--s-5) var(--s-6)",
            display: "grid",
            gap: "var(--s-3)",
            borderLeft: "4px solid var(--accent-gold)",
          }}
        >
          <Eyebrow>Open the graph for a verified case</Eyebrow>
          <p
            style={{
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              color: "var(--ink-2)",
              fontSize: "1.05rem",
              margin: 0,
              maxWidth: "62ch",
            }}
          >
            Pick any case below to render its full provenance graph —
            company at the centre, warning signs by severity, and the
            exact source records that triggered each one.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-2)" }}>
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
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-3)", fontSize: "0.72rem" }}>
                    {d.evidence} signals →
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {query.isLoading && <LoadingCanvas />}
      {query.isError && (
        <p style={{ color: "var(--risk-critical)" }}>
          {(query.error as Error).message}
        </p>
      )}

      {query.data && (
        <section
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) 320px",
            gap: "var(--s-5)",
          }}
        >
          {/* Canvas */}
          <div
            style={{
              background: "var(--paper-elevated)",
              boxShadow: "var(--shadow-card)",
              padding: "var(--s-3)",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                padding: "var(--s-3) var(--s-4) 0",
              }}
            >
              <Eyebrow>
                {query.data.signal_count} warning sign{query.data.signal_count === 1 ? "" : "s"} ·{" "}
                {query.data.triggered_by.length} source record{query.data.triggered_by.length === 1 ? "" : "s"}
              </Eyebrow>
              <div style={{ display: "inline-flex", alignItems: "center", gap: "var(--s-3)" }}>
                <Eyebrow style={{ color: "var(--ink-4)" }}>
                  Hover - Click to highlight evidence
                </Eyebrow>
                <button
                  type="button"
                  onClick={() => api.downloadProvenance(query.data.cin, query.data)}
                  style={{
                    background: "transparent",
                    border: `1px solid ${HEX.accentGold}`,
                    color: HEX.accentGold,
                    fontSize: "0.72rem",
                    fontWeight: 700,
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                    padding: "4px 10px",
                    cursor: "pointer",
                  }}
                >
                  Export JSON
                </button>
              </div>
            </div>

            {highlightedIds.size > 0 && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "var(--s-2) var(--s-4)",
                  background: "rgba(160,113,39,0.12)",
                  borderLeft: `3px solid ${HEX.accentGold}`,
                  gap: "var(--s-4)",
                }}
              >
                <span style={{ fontSize: "0.78rem", color: HEX.accentGold, fontWeight: 600, letterSpacing: "0.08em" }}>
                  Showing evidence for: {inspected?.label?.slice(0, 50)}
                </span>
                <button
                  onClick={() => setSelectedId(null)}
                  style={{
                    background: "transparent",
                    border: `1px solid ${HEX.accentGold}`,
                    color: HEX.accentGold,
                    fontSize: "0.72rem",
                    fontWeight: 700,
                    letterSpacing: "0.14em",
                    textTransform: "uppercase",
                    padding: "3px 10px",
                    cursor: "pointer",
                  }}
                >
                  Clear
                </button>
              </div>
            )}

            <svg
              viewBox={viewBox}
              preserveAspectRatio="xMidYMid meet"
              style={{
                display: "block",
                width: "100%",
                height: HEIGHT,
                background:
                  "radial-gradient(circle at 30% 30%, var(--paper-deep) 0%, var(--paper-elevated) 60%, var(--paper) 100%)",
              }}
              onClick={() => setSelectedId(null)}
            >
              <defs>
                {/* Subtle ink-on-paper drop-shadow. */}
                <filter id="ink-shadow" x="-20%" y="-20%" width="140%" height="140%">
                  <feDropShadow
                    dx="0"
                    dy="2"
                    stdDeviation="2.5"
                    floodColor="#0e0d0a"
                    floodOpacity="0.22"
                  />
                </filter>
                {/* Hairline arrowhead for directed edges. */}
                <marker
                  id="arrow"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M0,0 L10,5 L0,10 z" fill={HEX.ink3} />
                </marker>
                <radialGradient id="company-glow" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#0e0d0a" />
                  <stop offset="100%" stopColor="#3b332a" />
                </radialGradient>
                <pattern
                  id="paper-grain"
                  width="6"
                  height="6"
                  patternUnits="userSpaceOnUse"
                >
                  <rect width="6" height="6" fill="transparent" />
                  <circle cx="1" cy="1" r="0.3" fill="#0e0d0a" opacity="0.06" />
                  <circle cx="4" cy="3" r="0.25" fill="#0e0d0a" opacity="0.06" />
                </pattern>
                {/* Paper grain overlay rect (positioned outside <defs> below). */}
              </defs>

              {/* Subtle grain over the canvas. */}
              <rect
                x={vbParts[0]}
                y={vbParts[1]}
                width={vbParts[2]}
                height={vbParts[3]}
                fill="url(#paper-grain)"
                pointerEvents="none"
              />

              {/* Edges */}
              <g>
                {links.map((lnk, i) => {
                  const s =
                    nodes.find((n) => n.id === lnk.source) ??
                    (typeof lnk.source !== "string"
                      ? (lnk.source as unknown as GraphNode)
                      : undefined);
                  const t =
                    nodes.find((n) => n.id === lnk.target) ??
                    (typeof lnk.target !== "string"
                      ? (lnk.target as unknown as GraphNode)
                      : undefined);
                  if (!s || !t || s.x === undefined || t.x === undefined)
                    return null;
                  const involvesHover =
                    hoveredId !== null &&
                    (hoveredId === s.id || hoveredId === t.id);
                  const isHighlighted =
                    highlightedIds.size > 0 &&
                    highlightedIds.has(s.id) &&
                    highlightedIds.has(t.id);
                  const mx = ((s.x ?? 0) + (t.x ?? 0)) / 2;
                  const my = ((s.y ?? 0) + (t.y ?? 0)) / 2;
                  const edgeLabel =
                    lnk.label === "HAS_FRAUD_SIGNAL" ? "fraud finding" : "source record";
                  const showLabel = involvesHover || isHighlighted;
                  return (
                    <g key={i}>
                      <line
                        x1={s.x}
                        y1={s.y}
                        x2={t.x}
                        y2={t.y}
                        stroke={isHighlighted ? HEX.accentGold : involvesHover ? HEX.accentGold : HEX.ink3}
                        strokeWidth={isHighlighted ? 2.2 : involvesHover ? 1.6 : 0.9}
                        strokeOpacity={isHighlighted ? 1 : involvesHover ? 1 : 0.55}
                        markerEnd="url(#arrow)"
                      />
                      {showLabel && (
                        <g pointerEvents="none">
                          <rect
                            x={mx - 38}
                            y={my - 8}
                            width={76}
                            height={14}
                            fill={HEX.paper}
                            opacity={0.88}
                          />
                          <text
                            x={mx}
                            y={my + 3}
                            textAnchor="middle"
                            fontFamily='"JetBrains Mono", ui-monospace, monospace'
                            fontSize={9}
                            fill={HEX.accentGold}
                          >
                            {edgeLabel}
                          </text>
                        </g>
                      )}
                    </g>
                  );
                })}
              </g>

              {/* Nodes */}
              <g>
                {nodes.map((n) => {
                  if (n.x === undefined) return null;
                  const isHover = hoveredId === n.id;
                  const isSelected = selectedId === n.id;
                  const isHighlighted = highlightedIds.has(n.id) && !isSelected;
                  const baseR =
                    n.kind === "company" ? 26 : n.kind === "signal" ? 14 : 11;
                  const r = isHover || isSelected ? baseR + 4 : baseR;
                  const fillRef =
                    n.kind === "company" ? "url(#company-glow)" : n.fill;

                  // Label visibility rules:
                  // - Company: always full label
                  // - Signal: always show (compact when idle, full on hover/select)
                  // - Ref: only on hover or select
                  const showLabel =
                    n.kind === "company" ||
                    n.kind === "signal" ||
                    isHover ||
                    isSelected;
                  const labelText =
                    isHover || isSelected || n.kind === "company"
                      ? n.label.slice(0, 38)
                      : n.label.slice(0, 20);
                  const labelWidth = Math.max(
                    40,
                    Math.min(labelText.length, isHover || isSelected ? 38 : 22) * 6.5,
                  );

                  return (
                    <g
                      key={n.id}
                      transform={`translate(${n.x},${n.y})`}
                      onMouseEnter={() => setHoveredId(n.id)}
                      onMouseLeave={() =>
                        setHoveredId((cur) => (cur === n.id ? null : cur))
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedId((cur) => (cur === n.id ? null : n.id));
                      }}
                      style={{ cursor: "pointer" }}
                      filter={isHover || isSelected || isHighlighted ? "url(#ink-shadow)" : undefined}
                    >
                      {/* Evidence highlight halo — gold outer ring */}
                      {isHighlighted && (
                        <circle
                          r={r + 5}
                          fill="none"
                          stroke={HEX.accentGold}
                          strokeWidth={2}
                          strokeOpacity={0.85}
                          strokeDasharray="3 2"
                        />
                      )}
                      <circle
                        r={r}
                        fill={fillRef}
                        stroke={
                          isSelected
                            ? HEX.accentGold
                            : isHighlighted
                            ? HEX.accentGold
                            : HEX.paper
                        }
                        strokeWidth={isSelected ? 2.5 : isHighlighted ? 1.8 : 1.6}
                        style={{
                          transition:
                            "r var(--d-quick) var(--ease-out), stroke var(--d-quick)",
                        }}
                      />
                      {/* Company bullseye glyph */}
                      {n.kind === "company" && (
                        <circle r={5} fill={HEX.paper} pointerEvents="none" />
                      )}
                      <title>{n.label}</title>
                      {showLabel && (
                        <g pointerEvents="none">
                          <rect
                            x={r + 6}
                            y={-9}
                            width={labelWidth}
                            height={18}
                            fill={HEX.paper}
                            stroke={isHighlighted ? HEX.accentGold : HEX.rule}
                            strokeWidth={isHighlighted ? 1 : 0.6}
                            rx={0}
                          />
                          <text
                            x={r + 11}
                            y={4}
                            fontFamily='"JetBrains Mono", ui-monospace, monospace'
                            fontSize={isHover || isSelected ? 11 : 9}
                            fill={isHighlighted ? HEX.accentGold : HEX.ink}
                          >
                            {labelText}
                          </text>
                        </g>
                      )}
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>

          {/* Inspector rail */}
          <aside
            style={{
              display: "grid",
              gap: "var(--s-4)",
              alignContent: "start",
            }}
          >
            <Legend />
            <Inspector node={inspected} />
          </aside>
        </section>
      )}
    </article>
  );
}

function Legend() {
  const items = [
    {
      label: "Company",
      sub: "the subject under review",
      swatch: <span style={dotStyle("var(--ink)", 14)} />,
    },
    {
      label: "Warning sign",
      sub: "colour shows severity · click to highlight evidence",
      swatch: (
        <span style={{ display: "inline-flex", gap: 3 }}>
          <span style={dotStyle("var(--risk-low)", 9)} />
          <span style={dotStyle("var(--risk-medium)", 9)} />
          <span style={dotStyle("var(--risk-high)", 9)} />
          <span style={dotStyle("var(--risk-critical)", 9)} />
        </span>
      ),
    },
    {
      label: "Source record",
      sub: "the data that triggered the warning",
      swatch: <span style={dotStyle("#1e3a8a", 9)} />,
    },
  ];
  return (
    <section
      style={{
        background: "var(--paper-elevated)",
        boxShadow: "var(--shadow-card)",
        padding: "var(--s-4) var(--s-5)",
        display: "grid",
        gap: "var(--s-3)",
      }}
    >
      <Eyebrow>Legend</Eyebrow>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: "var(--s-3)" }}>
        {items.map((it) => (
          <li
            key={it.label}
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "var(--s-3)",
              alignItems: "center",
            }}
          >
            <span aria-hidden style={{ display: "inline-flex" }}>
              {it.swatch}
            </span>
            <span>
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: "1.05rem",
                  color: "var(--ink)",
                }}
              >
                {it.label}
              </span>
              <br />
              <span style={{ fontSize: "0.78rem", color: "var(--ink-3)" }}>
                {it.sub}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function dotStyle(color: string, size: number): React.CSSProperties {
  return {
    width: size,
    height: size,
    background: color,
    borderRadius: 999,
    display: "inline-block",
  };
}

function Inspector({ node }: { node: GraphNode | null }) {
  return (
    <section
      style={{
        background: "var(--paper-elevated)",
        boxShadow: "var(--shadow-card)",
        padding: "var(--s-5)",
        minHeight: 200,
        display: "grid",
        gap: "var(--s-3)",
        alignContent: "start",
      }}
    >
      <Eyebrow>Inspector</Eyebrow>
      <AnimatePresence mode="wait">
        {node === null ? (
          <motion.p
            key="empty"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              fontFamily: "var(--font-display)",
              fontStyle: "italic",
              color: "var(--ink-3)",
              fontSize: "1rem",
            }}
          >
            Hover any dot to see what it represents. Click to pin it open — clicking a warning-sign node also highlights its full evidence trail in gold.
          </motion.p>
        ) : (
          <motion.div
            key={node.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            style={{ display: "grid", gap: "var(--s-3)" }}
          >
            <h3
              style={{
                fontSize: "1.4rem",
                color: "var(--ink)",
                wordBreak: "break-word",
              }}
            >
              {node.label}
            </h3>
            <dl
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr",
                rowGap: 6,
                columnGap: "var(--s-3)",
                margin: 0,
                fontSize: "0.85rem",
              }}
            >
              {Object.entries(node.meta).map(([k, v]) => (
                <FragmentRow key={k} label={k} value={v} />
              ))}
            </dl>
            {node.evidenceString && (
              <>
                <div className="rule-gold" aria-hidden />
                <p
                  style={{
                    fontFamily: "var(--font-display)",
                    fontStyle: "italic",
                    color: "var(--ink)",
                    lineHeight: 1.5,
                  }}
                >
                  {node.evidenceString}
                </p>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function FragmentRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt
        style={{
          fontFamily: "var(--font-body)",
          fontSize: "0.68rem",
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "var(--ink-4)",
          fontWeight: 600,
          whiteSpace: "nowrap",
          alignSelf: "center",
        }}
      >
        {label}
      </dt>
      <dd
        style={{
          margin: 0,
          fontFamily: "var(--font-mono)",
          color: "var(--ink-2)",
          wordBreak: "break-word",
        }}
      >
        {value}
      </dd>
    </>
  );
}

function LoadingCanvas() {
  return (
    <div
      style={{
        background: "var(--paper-elevated)",
        boxShadow: "var(--shadow-card)",
        padding: "var(--s-6) var(--s-6)",
        display: "grid",
        gap: "var(--s-4)",
        minHeight: 380,
      }}
    >
      <Eyebrow>Loading evidence graph…</Eyebrow>
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontStyle: "italic",
          color: "var(--ink-3)",
          fontSize: "1.1rem",
        }}
      >
        Building the evidence map — connecting each warning sign to the records that triggered it.
      </p>
    </div>
  );
}
