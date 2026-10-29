/**
 * Praxis — Sites deployment worker.
 *
 * Serves the built React SPA as static assets and proxies `/api/*` to the
 * FastAPI backend, which cannot run on Sites (there is no Python runtime).
 * Keeping the proxy on the same origin is what makes the rest simple:
 *   - the browser only talks to one origin, so no CORS is needed
 *   - the session cookie stays a normal same-site cookie
 *   - the backend URL lives in exactly one place: the BACKEND_URL secret
 *
 * Set it with: Sites → Secrets → BACKEND_URL, e.g. https://praxis-api.onrender.com
 */
const SECURITY_HEADERS = {
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
  "referrer-policy": "no-referrer",
  "permissions-policy": "camera=(), microphone=(), geolocation=()",
  "cross-origin-opener-policy": "same-origin",
};

const API_PREFIX = "/api";

function backendNotConfigured() {
  return new Response(
    JSON.stringify({
      error: "BACKEND_NOT_CONFIGURED",
      message:
        "This site has no API target yet. Set the BACKEND_URL secret to the deployed " +
        "FastAPI host (Sites → Secrets) and redeploy.",
    }),
    { status: 503, headers: { "content-type": "application/json; charset=utf-8", ...SECURITY_HEADERS } },
  );
}

function withSecurityHeaders(response, request) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    if (!headers.has(name)) headers.set(name, value);
  }
  // Static assets are immutable per build; the SPA shell must never be cached.
  const isAsset = new URL(request.url).pathname.startsWith("/assets/");
  headers.set("cache-control", isAsset ? "public, max-age=31536000, immutable" : "no-cache");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.startsWith(API_PREFIX)) {
      if (!env.BACKEND_URL) return backendNotConfigured();

      const backend = new URL(env.BACKEND_URL);
      const target = new URL(request.url);
      target.protocol = backend.protocol;
      target.host = backend.host;

      const headers = new Headers(request.headers);
      headers.delete("host");
      headers.set("x-forwarded-proto", "https");
      // The API derives the client address from the first X-Forwarded-For entry
      // for rate limiting and audit records, so a browser-supplied value must
      // never reach it: replace it with Cloudflare's verified connecting IP.
      const clientIp = request.headers.get("cf-connecting-ip");
      if (clientIp) headers.set("x-forwarded-for", clientIp);
      else headers.delete("x-forwarded-for");

      const upstream = await fetch(target.toString(), {
        method: request.method,
        headers,
        body: request.body,
        redirect: "manual",
        // @ts-ignore duplex is required by undici when a body is streamed
        duplex: "half",
      }).catch(() => null);

      if (!upstream) {
        return new Response(
          JSON.stringify({
            error: "API_UNREACHABLE",
            message: "The API host did not respond. Check the BACKEND_URL secret.",
          }),
          { status: 502, headers: { "content-type": "application/json; charset=utf-8", ...SECURITY_HEADERS } },
        );
      }

      const responseHeaders = new Headers(upstream.headers);
      responseHeaders.delete("x-frame-options");
      responseHeaders.set("x-frame-options", "DENY");
      responseHeaders.set("x-content-type-options", "nosniff");
      responseHeaders.set("cache-control", "no-store");
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    }

    return withSecurityHeaders(await env.ASSETS.fetch(request), request);
  },
};
