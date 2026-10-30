import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, errorMessage, fieldErrors } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrf_token=test-csrf-token" });
  vi.stubGlobal("window", { dispatchEvent: vi.fn() });
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client", () => {
  it("sends the CSRF double-submit header on unsafe methods", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await api("/practice/tests", { method: "POST", body: { title: "Test" } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/practice/tests");
    expect(init.headers["X-CSRF-Token"]).toBe("test-csrf-token");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.credentials).toBe("same-origin");
  });

  it("never sends a bearer token from script-readable storage", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await api("/auth/me");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("fetches a CSRF token first when the cookie is missing", async () => {
    vi.stubGlobal("document", { cookie: "" });
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "fresh-token" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api("/practice/tests", { method: "POST", body: {} });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/csrf");
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("fresh-token");
  });

  it("does not attach CSRF on GET", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await api("/health");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("raises a typed API error with server code and details", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: "VALIDATION_ERROR", message: "Invalid blueprint.", details: [{ field: "total_marks" }] }, 422),
    );
    try {
      await api("/exams", { method: "POST", body: {} });
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(422);
      expect((error as ApiError).code).toBe("VALIDATION_ERROR");
      expect(fieldErrors(error)).toEqual(["total_marks: undefined"]);
      expect(errorMessage(error)).toBe("Invalid blueprint.");
    }
  });

  it("handles empty 204 responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("dispatches an unauthorized event on 401", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "UNAUTHORIZED", message: "Sign in." }, 401));
    await expect(api("/auth/me")).rejects.toThrow("Sign in.");
    expect(window.dispatchEvent).toHaveBeenCalled();
  });
});
