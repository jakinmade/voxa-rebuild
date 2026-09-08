/**
 * voicova-seo-worker.js
 *
 * WHY THIS EXISTS
 * voicova.com currently resolves straight to Railway with nothing in
 * front of it. Streamlit ships a blank <title>Streamlit</title> shell
 * with no meta description or OG tags in the raw HTML - the real
 * title/description only get added client-side by JavaScript after
 * the page loads (see the _seo_meta_injected block in app.py, shipped
 * 31 Aug 2026). That's invisible to any crawler that doesn't execute
 * JS, and unreliable/delayed even for ones that do (Google's JS
 * rendering is a slower second pass, not guaranteed).
 *
 * This Worker sits in front of voicova.com as a reverse proxy:
 *   - /robots.txt and /sitemap.xml are answered directly, in plain
 *     text, with zero dependency on Streamlit.
 *   - Every other request is proxied straight through to the real
 *     Railway origin, UNCHANGED, except the HTML <head> gets the
 *     title/description/OG/canonical tags rewritten server-side,
 *     using Cloudflare's streaming HTMLRewriter - so they're present
 *     in the very first byte any crawler (or link-preview bot) sees,
 *     no JavaScript required.
 *   - The existing client-side JS injection in app.py can stay as a
 *     harmless backup; this Worker makes it no longer load-bearing.
 *
 * DEPLOY (Cloudflare dashboard, ~2 minutes, no CLI needed):
 *   1. dash.cloudflare.com -> your account -> Workers & Pages
 *   2. Create -> Create Worker -> name it "voicova-seo" -> Deploy
 *   3. Edit code -> paste this whole file over the default template -> Save and Deploy
 *   4. Go to the voicova.com zone -> Rules -> (or the Worker's own
 *      "Triggers" tab) -> Add Route:
 *         voicova.com/*      -> voicova-seo
 *         www.voicova.com/*  -> voicova-seo
 *      (voicova.com must already be proxied through Cloudflare -
 *      orange cloud - which it is, since Cloudflare already handles
 *      your SSL. No DNS change needed - the Route intercepts at the
 *      edge before the request reaches Railway.)
 *   5. Visit https://voicova.com/robots.txt and view-source on
 *      https://voicova.com/ to confirm the title/meta are now in the
 *      raw HTML.
 *
 * If anything looks wrong after deploying, the fastest rollback is
 * just deleting the two Routes in step 4 - traffic falls straight
 * back to today's exact behaviour with zero other changes.
 */

const ORIGIN = "https://web-production-8022c.up.railway.app";

const SITE_TITLE = "Voicova - Communication Identity";
const SITE_DESCRIPTION =
  "Voicova preserves who you are when you write. Test any draft against your own voice fingerprint and fix what doesn't sound like you.";
const SITE_URL = "https://voicova.com";

const ROBOTS_TXT = `User-agent: *
Allow: /

Sitemap: https://voicova.com/sitemap.xml
`;

const SITEMAP_XML = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://voicova.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
`;

// Streaming HTML rewriter: sets <title>, upserts meta/link tags in
// <head>. Runs on every HTML response the Worker proxies through, so
// it applies regardless of which screen state Streamlit happens to
// render first.
class HeadRewriter {
  element(el) {
    if (el.tagName === "title") {
      el.setInnerContent(SITE_TITLE);
    }
  }
}

class HeadInjector {
  element(el) {
    // Runs once, right after <head> opens - appends every tag we
    // need in one shot rather than trying to find/replace individual
    // existing (possibly absent) meta tags.
    el.append(
      `<meta name="description" content="${escapeAttr(SITE_DESCRIPTION)}">` +
        `<meta property="og:title" content="${escapeAttr(SITE_TITLE)}">` +
        `<meta property="og:description" content="${escapeAttr(SITE_DESCRIPTION)}">` +
        `<meta property="og:type" content="website">` +
        `<meta property="og:url" content="${SITE_URL}">` +
        `<meta name="twitter:card" content="summary">` +
        `<link rel="canonical" href="${SITE_URL}">`,
      { html: true }
    );
  }
}

function escapeAttr(s) {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/robots.txt") {
      return new Response(ROBOTS_TXT, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    if (url.pathname === "/sitemap.xml") {
      return new Response(SITEMAP_XML, {
        headers: { "content-type": "application/xml; charset=utf-8" },
      });
    }

    // Proxy everything else straight through to Railway, unchanged
    // except for the HTML head rewrite below.
    //
    // 1 Sep 2026 fix: fetch(originUrl, request) lets Cloudflare set the
    // outgoing Host header to the target URL's hostname
    // (web-production-8022c.up.railway.app). Streamlit's WebSocket
    // handshake validates that the browser's Origin header matches the
    // Host header IT sees - so every WS connection through this Worker
    // was getting rejected ("Rejecting WebSocket connection with
    // disallowed Origin or Host header"), regardless of any
    // --server.enableCORS / --server.enableXsrfProtection flag on the
    // Streamlit side (confirmed - disabling both did not fix it, so
    // the mismatch has to be fixed here at the proxy, not the app).
    // Explicitly forcing Host back to the original public hostname
    // makes Origin and Host match again. Railway already has both its
    // own subdomain and voicova.com/www.voicova.com configured as
    // domains on this one service, so it routes correctly either way.
    const originUrl = ORIGIN + url.pathname + url.search;
    const proxyRequest = new Request(originUrl, request);
    proxyRequest.headers.set("Host", url.hostname);
    const originResponse = await fetch(proxyRequest);

    const contentType = originResponse.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      // Non-HTML (Streamlit's websocket/static asset traffic etc.) -
      // pass through completely untouched.
      return originResponse;
    }

    return new HTMLRewriter()
      .on("title", new HeadRewriter())
      .on("head", new HeadInjector())
      .transform(originResponse);
  },
};
