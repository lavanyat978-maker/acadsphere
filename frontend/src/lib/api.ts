export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

// Session token handling.
//
// The backend sets an HttpOnly cookie, which is the right default: JavaScript
// cannot read it, so XSS cannot exfiltrate the session. Some embedded preview
// contexts (an iframe without `allow-same-origin`, a cross-site proxy) refuse
// to persist cookies at all, and then every authenticated call fails even
// though login returned 200. In that case we fall back to carrying the token
// explicitly as `Authorization: Bearer`, which the backend accepts, and which
// the CSRF middleware exempts because it is not cookie-driven.
const TOKEN_KEY = "praxis.access_token";
let memoryToken = "";

export function setToken(token: string): void {
  memoryToken = token;
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage unavailable (sandboxed frame): memory token still works */
  }
}

export function clearToken(): void {
  memoryToken = "";
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export function getToken(): string {
  if (memoryToken) return memoryToken;
  try {
    memoryToken = localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    memoryToken = "";
  }
  return memoryToken;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  raw?: boolean;
};

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  } else if (method !== "GET") {
    headers["X-CSRF-Token"] = readCookie("csrf_token");
  }
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body,
    signal: options.signal,
    credentials: "same-origin",
  });

  if (response.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/mfa")) {
    clearToken();
    window.dispatchEvent(new CustomEvent("praxis:unauthorized"));
  }

  if (!response.ok) {
    let payload: { error?: string; message?: string; details?: unknown } = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    throw new ApiError(
      response.status,
      payload.error ?? `HTTP_${response.status}`,
      payload.message ?? `Request failed with status ${response.status}.`,
      payload.details,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return (await response.blob()) as T;
  }
  return (await response.json()) as T;
}

export async function downloadPdf(path: string): Promise<void> {
  const token = getToken();
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    let message = "Unable to generate the printable copy.";
    try {
      const payload = await response.json();
      message = payload.message ?? message;
    } catch {
      /* binary/empty error */
    }
    throw new ApiError(response.status, "PDF_FAILED", message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = response.headers.get("content-disposition")?.match(/filename="(.+)"/)?.[1] ?? "paper.pdf";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

/** Turn 422 details into a readable field list. */
export function fieldErrors(error: unknown): string[] {
  if (error instanceof ApiError && Array.isArray(error.details)) {
    return (error.details as Array<{ field?: string; message?: string }>).map((detail) =>
      detail.field ? `${detail.field}: ${detail.message}` : String(detail.message),
    );
  }
  return [];
}
