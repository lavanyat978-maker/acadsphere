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

// Session handling.
//
// The session lives only in the HttpOnly `access_token` cookie set by the API,
// so page JavaScript (and therefore an XSS payload) can never read it. Unsafe
// requests carry the double-submit CSRF token: the readable `csrf_token`
// cookie mirrored into the X-CSRF-Token header.
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

async function csrfToken(): Promise<string> {
  const existing = readCookie("csrf_token");
  if (existing) return existing;
  try {
    const response = await fetch("/api/auth/csrf", { credentials: "same-origin" });
    const payload = (await response.json()) as { csrf_token?: string };
    return readCookie("csrf_token") || payload.csrf_token || "";
  } catch {
    return "";
  }
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
  if (UNSAFE.has(method)) {
    headers["X-CSRF-Token"] = await csrfToken();
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
    window.dispatchEvent(new CustomEvent("praxis:unauthorized"));
  }

  if (!response.ok) {
    let payload: { error?: string; message?: string; details?: unknown } = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (response.status === 403 && payload.error === "PASSWORD_CHANGE_REQUIRED") {
      window.dispatchEvent(new CustomEvent("praxis:password-change-required"));
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
  const response = await fetch(`/api${path}`, { credentials: "same-origin" });
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
