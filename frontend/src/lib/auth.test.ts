// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadReport } from "./api";

const TOKEN_KEY = "sentinelg.jwt";

const originalFetch = globalThis.fetch;
const originalCreateObjectURL = (globalThis.URL as unknown as { createObjectURL?: (b: Blob) => string }).createObjectURL;
const originalRevokeObjectURL = (globalThis.URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL;

beforeEach(() => {
  localStorage.clear();
  (globalThis.URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL = vi.fn(() => "blob:fake");
  (globalThis.URL as unknown as { revokeObjectURL: (u: string) => void }).revokeObjectURL = vi.fn();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalCreateObjectURL) {
    (globalThis.URL as unknown as { createObjectURL?: (b: Blob) => string }).createObjectURL = originalCreateObjectURL;
  } else {
    delete (globalThis.URL as unknown as { createObjectURL?: (b: Blob) => string }).createObjectURL;
  }
  if (originalRevokeObjectURL) {
    (globalThis.URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL = originalRevokeObjectURL;
  } else {
    delete (globalThis.URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL;
  }
});

describe("bearer header propagation", () => {
  it("api requests carry Authorization when a token is stored", async () => {
    localStorage.setItem(TOKEN_KEY, "test-jwt");
    const stub = vi.fn().mockResolvedValue({
      ok: true, status: 200, statusText: "ok",
      json: async () => ({}), text: async () => "{}",
    });
    globalThis.fetch = stub as unknown as typeof fetch;
    // Re-import lazily so the bearer header reads the new localStorage state.
    const { api } = await import("./api");
    await api.health();
    const init = stub.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-jwt");
  });

  it("api requests omit Authorization when no token", async () => {
    const stub = vi.fn().mockResolvedValue({
      ok: true, status: 200, statusText: "ok",
      json: async () => ({}), text: async () => "{}",
    });
    globalThis.fetch = stub as unknown as typeof fetch;
    const { api } = await import("./api");
    await api.health();
    const init = stub.mock.calls[0][1] as RequestInit;
    const headers = (init.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });
});

describe("downloadReport", () => {
  it("returns audit headers and triggers a save-as", async () => {
    const stub = vi.fn().mockResolvedValue({
      ok: true, status: 200, statusText: "ok",
      blob: async () => new Blob(["%PDF-1.4 fake"], { type: "application/pdf" }),
      text: async () => "",
      headers: {
        get(name: string): string | null {
          if (name.toLowerCase() === "x-report-id") return "report-uuid-1";
          if (name.toLowerCase() === "x-report-generated-at") return "2026-05-14T10:00:00";
          return null;
        },
      },
    });
    globalThis.fetch = stub as unknown as typeof fetch;
    const result = await downloadReport("U45201MH2005PTC155294");
    expect(stub.mock.calls[0][0]).toBe("/api/report/U45201MH2005PTC155294");
    expect(result.reportId).toBe("report-uuid-1");
    expect(result.generatedAt).toBe("2026-05-14T10:00:00");
  });

  it("throws on non-2xx", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false, status: 401, statusText: "Unauthorized",
      text: async () => "not authenticated",
    }) as unknown as typeof fetch;
    await expect(downloadReport("X")).rejects.toThrow(/401/);
  });
});
