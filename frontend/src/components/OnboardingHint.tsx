/* Single-time dismissible banner shown on first login.
 *
 * Renders a one-line nudge pointing at IL&FS as the "try this first"
 * path. Dismissal pins to localStorage so subsequent sessions don't
 * see it. Lives on the Dashboard above the score plate (or above the
 * QuickStartPanel when no CIN is active).
 *
 * Purposefully zero-state: no animation library beyond what the rest
 * of the page already imports. Just a styled <aside>.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { DEMO_CASES } from "../lib/demoCases";

const STORAGE_KEY = "sentinelg.onboarding.dismissed";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function writeDismissed() {
  try {
    localStorage.setItem(STORAGE_KEY, "true");
  } catch {
    /* localStorage disabled — no-op */
  }
}

export default function OnboardingHint() {
  const [dismissed, setDismissed] = useState<boolean>(() => readDismissed());

  if (dismissed) return null;

  // Pick the first CRITICAL case as the "try this" anchor.
  const showcase = DEMO_CASES.find((c) => c.band === "CRITICAL") ?? DEMO_CASES[0];

  return (
    <aside
      role="status"
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: "var(--s-4)",
        padding: "var(--s-4) var(--s-5)",
        background: "var(--paper-elevated)",
        borderTop: "1px solid var(--accent-gold)",
        borderBottom: "1px solid var(--accent-gold)",
        boxShadow: "var(--shadow-card)",
        alignItems: "center",
      }}
    >
      <div style={{ display: "grid", gap: 4 }}>
        <span style={{
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color: "var(--accent-gold)",
          fontWeight: 700,
        }}>
          First time here?
        </span>
        <p style={{
          margin: 0,
          fontFamily: "var(--font-display)",
          fontStyle: "italic",
          fontSize: "1.05rem",
          color: "var(--ink-2)",
          lineHeight: 1.4,
        }}>
          Try{" "}
          <Link
            to={showcase.route}
            style={{
              color: "var(--ink)",
              fontWeight: 700,
              textDecoration: "underline",
              textDecorationColor: "var(--accent-gold)",
              textUnderlineOffset: 3,
            }}
          >
            {showcase.name}
          </Link>{" "}
          — the engine catches {showcase.evidence} fraud signals in under
          3 seconds. {showcase.blurb}.
        </p>
      </div>
      <button
        type="button"
        onClick={() => {
          writeDismissed();
          setDismissed(true);
        }}
        aria-label="Dismiss onboarding hint"
        style={{
          background: "transparent",
          border: "1px solid var(--rule-soft)",
          color: "var(--ink-3)",
          fontFamily: "var(--font-body)",
          fontSize: "var(--t-eyebrow)",
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          fontWeight: 600,
          padding: "6px 14px",
          cursor: "pointer",
          borderRadius: 0,
        }}
      >
        Dismiss
      </button>
    </aside>
  );
}
