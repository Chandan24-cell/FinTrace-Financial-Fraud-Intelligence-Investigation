/* NarrativeCard — Stream 2.5 of the production-grade closure plan.
 *
 * Renders the Mistral executive summary for one CIN. Mounted on
 * the Dashboard between the ScorePlate hero and the ModuleBreakdown
 * chart — gives the analyst a 90-140 word forensic-audit synopsis
 * before they dive into the bars + chain.
 *
 * Graceful degradation contract:
 *   - Initial paint: skeleton stripe so the layout doesn't shift when
 *     the narrative resolves.
 *   - Rate-limited / no API key: backend serves deterministic template
 *     prose internally; the dashboard still renders the synopsis.
 *   - Network or 5xx error: inline error pill, hero/chain still render.
 *   - 401: lets the layout's <ProtectedRoute> catch it via the standard
 *     redirect — same as any other dashboard surface.
 */
import { useQuery } from "@tanstack/react-query";
import { api, type NarrativeResponse } from "../lib/api";

interface NarrativeCardProps {
  cin: string;
}

function SkeletonStripe() {
  return (
    <div
      aria-hidden
      style={{
        height: 16,
        borderRadius: 0,
        background:
          "linear-gradient(90deg, var(--paper-deep) 0%, var(--paper-elevated) 50%, var(--paper-deep) 100%)",
        backgroundSize: "200% 100%",
        animation: "narrative-shimmer 1.4s ease-in-out infinite",
        marginBottom: "var(--s-2)",
      }}
    />
  );
}


export default function NarrativeCard({ cin }: NarrativeCardProps) {
  const query = useQuery<NarrativeResponse>({
    queryKey: ["narrative", cin],
    queryFn: () => api.narrative(cin),
    enabled: Boolean(cin),
    // Same evidence-hash cache lives on the backend; client-side
    // staleTime of 5 min stops the dashboard from re-fetching during
    // tab navigation.
    staleTime: 5 * 60_000,
    retry: 1,
  });

  return (
    <section
      aria-label="Forensic narrative"
      style={{
        display: "grid",
        gap: "var(--s-3)",
        background: "var(--paper-elevated)",
        padding: "var(--s-5) var(--s-6)",
        borderTop: "1px solid var(--rule)",
        borderBottom: "1px solid var(--rule-soft)",
        boxShadow: "var(--shadow-card)",
      }}
    >
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-4)" }}>
        <span
          style={{
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-eyebrow)",
            letterSpacing: "0.28em",
            textTransform: "uppercase",
            color: "var(--accent-gold)",
            fontWeight: 700,
          }}
        >
          Forensic synopsis · PRD §5.5
        </span>
      </header>

      {query.isLoading && (
        <div>
          <SkeletonStripe />
          <SkeletonStripe />
          <SkeletonStripe />
          <p style={{ color: "var(--ink-4)", margin: 0, fontFamily: "var(--font-body)", fontSize: "var(--t-meta)" }}>
            Composing executive summary…
          </p>
        </div>
      )}

      {query.isError && (
        <p
          role="alert"
          style={{
            color: "var(--risk-critical)",
            margin: 0,
            paddingLeft: "var(--s-3)",
            borderLeft: "2px solid var(--risk-critical)",
            fontFamily: "var(--font-body)",
            fontSize: "var(--t-meta)",
          }}
        >
          Narrative unavailable: {(query.error as Error).message}
        </p>
      )}

      {query.data && (
        <>
          <p
            style={{
              margin: 0,
              fontFamily: "var(--font-display)",
              fontSize: "1.05rem",
              lineHeight: 1.6,
              color: "var(--ink-2)",
              fontStyle: "italic",
              maxWidth: "62ch",
            }}
          >
            {query.data.summary}
          </p>
        </>
      )}

      {/* Inline keyframes — scoped enough that we don't pollute
       * tokens.css with one-off animation. */}
      <style>{`
        @keyframes narrative-shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </section>
  );
}
