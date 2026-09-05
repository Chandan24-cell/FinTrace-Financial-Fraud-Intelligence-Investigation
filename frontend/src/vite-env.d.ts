/// <reference types="vite/client" />

// Project-specific env-var hints for the Vite client. Keeps
// `import.meta.env.VITE_*` typed at build time. Day-28 added
// VITE_API_BASE so the frontend can point at the Oracle Cloud
// FastAPI URL in production.
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
