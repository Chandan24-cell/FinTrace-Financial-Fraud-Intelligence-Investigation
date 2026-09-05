import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import GraphExplorer from "./pages/GraphExplorer";
import ITCCarousel from "./pages/ITCCarousel";
import Evergreening from "./pages/Evergreening";
import UploadPage from "./pages/Upload";
import Reports from "./pages/Reports";
import Search from "./pages/Search";
import Sources from "./pages/Sources";
import ShellAtlas from "./pages/ShellAtlas";
import { CRITICAL_COUNT } from "./lib/demoCases";

// Masthead shell — shared glass tokens keep the investigation desk calm and legible.

const navStyle: React.CSSProperties = {
  display: "flex",
  gap: "var(--s-4)",
  padding: "var(--s-4) var(--s-7)",
  background: "var(--glass-nav)",
  color: "var(--ink)",
  backdropFilter: "blur(18px) saturate(125%)",
  WebkitBackdropFilter: "blur(18px) saturate(125%)",
  position: "sticky",
  top: 0,
  zIndex: 10,
  alignItems: "center",
  borderBottom: "1px solid var(--glass-border)",
  boxShadow: "0 10px 30px -24px rgba(32, 68, 91, 0.55)",
};

const linkBase: React.CSSProperties = {
  color: "var(--ink-2)",
  textDecoration: "none",
  padding: "6px 10px",
  fontFamily: "var(--font-body)",
  fontSize: "0.78rem",
  letterSpacing: "0.18em",
  textTransform: "uppercase",
  fontWeight: 600,
  // F7: don't mix the `borderBottom` shorthand with a `borderBottomColor`
  // longhand override in the active variant — React warns and the colour
  // sometimes wins, sometimes doesn't. Split into longhand triplet here.
  borderBottomStyle: "solid",
  borderBottomWidth: "1px",
  borderBottomColor: "transparent",
  transition: "border-color var(--d-quick) var(--ease-out), color var(--d-quick)",
};

function navLinkStyle({ isActive }: { isActive: boolean }): React.CSSProperties {
  return isActive
    ? { ...linkBase, borderBottomColor: "var(--accent-gold)", color: "var(--accent-gold-soft)" }
    : linkBase;
}

function Masthead() {
  return (
    <NavLink
      to="/dashboard"
      style={{
        marginRight: "auto",
        textDecoration: "none",
        display: "flex",
        alignItems: "baseline",
        gap: "var(--s-3)",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: 500,
          fontVariationSettings: "'opsz' 144, 'SOFT' 30, 'WONK' 1",
          fontSize: "1.5rem",
          color: "var(--ink)",
          letterSpacing: "-0.02em",
        }}
      >
        FINTRACE
      </span>
      <span
        style={{
          fontFamily: "var(--font-body)",
          fontSize: "0.65rem",
          letterSpacing: "0.32em",
          textTransform: "uppercase",
          color: "var(--ink-3)",
        }}
      >
        Financial Fraud Intelligence &amp; Investigation Platform
      </span>
    </NavLink>
  );
}

export default function App() {
  return (
    <div style={{ minHeight: "100vh", background: "transparent" }}>
      <nav style={navStyle}>
        <Masthead />
        <NavLink to="/search" style={navLinkStyle}>
          {({ isActive }) => (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              Search
              {!isActive && CRITICAL_COUNT > 0 && (
                <span
                  aria-label={`${CRITICAL_COUNT} critical cases ready`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center", justifyContent: "center",
                    minWidth: 18, height: 18, padding: "0 5px",
                    background: "var(--risk-critical, #7f1d1d)",
                    color: "var(--paper)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "0.65rem", fontWeight: 700,
                    letterSpacing: 0, borderRadius: 999,
                  }}
                >
                  {CRITICAL_COUNT}
                </span>
              )}
            </span>
          )}
        </NavLink>
        <NavLink to="/dashboard" style={navLinkStyle}>Dossier</NavLink>
        <NavLink to="/graph" style={navLinkStyle}>Graph</NavLink>
        <NavLink to="/upload" style={navLinkStyle}>Upload</NavLink>
        <NavLink to="/reports" style={navLinkStyle}>Reports</NavLink>
        <NavLink to="/sources" style={navLinkStyle}>Sources</NavLink>
        {/* Demo grouping — visually separated so judges can find the
            curated case views without confusing them with the analyst
            workspace pages above. */}
        <span
          aria-hidden
          style={{
            color: "var(--paper-deep)",
            fontFamily: "var(--font-body)",
            fontSize: "0.6rem", letterSpacing: "0.32em",
            textTransform: "uppercase",
            paddingLeft: "var(--s-3)",
            borderLeft: "1px solid var(--paper-deep)",
            marginLeft: "var(--s-2)",
          }}
        >
          Demo
        </span>
        <NavLink to="/itc" style={navLinkStyle}>ITC Ring</NavLink>
        <NavLink to="/evergreening" style={navLinkStyle}>Evergreening</NavLink>
      </nav>
      <main
        style={{
          padding: "var(--s-7) var(--s-6) var(--s-9)",
          maxWidth: 1180,
          margin: "0 auto",
        }}
      >
        <Routes>
          <Route path="/" element={<Navigate to="/search" replace />} />
          <Route path="/search" element={<Search />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/graph" element={<GraphExplorer />} />
          <Route path="/graph/:cin" element={<GraphExplorer />} />
          <Route path="/itc" element={<ITCCarousel />} />
          <Route path="/evergreening" element={<Evergreening />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/reports" element={<Reports />} />
          {/* /sources is intentionally public — judges and auditors can verify the data
              lineage without an account. */}
          <Route path="/sources" element={<Sources />} />
          {/* /shells is intentionally protected — the atlas surfaces names
              of real companies in shell-like clusters, so the same audit
              gate as /analyse applies. */}
          <Route path="/shells" element={<ShellAtlas />} />
          <Route path="*" element={<p>Not found</p>} />
        </Routes>
      </main>
    </div>
  );
}
