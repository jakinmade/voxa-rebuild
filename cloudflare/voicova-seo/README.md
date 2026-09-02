# voicova-seo (Cloudflare Worker)

Source-controlled backup of the live `voicova-seo` Worker's code,
pulled directly from Cloudflare on 2 Sept 2026 (hardening Session 2
infra audit) — **this file is not what's deployed.** Deployment is
manual, via the Cloudflare dashboard's inline editor, same as
`voicova-webhooks` and CLEARANCE's `clearance-monitor`. There is no
CI/CD link between this repo and the live Worker: editing `worker.js`
here does **not** update what Cloudflare is running, and editing the
Worker in the dashboard does **not** update this file. If the
dashboard version is ever hand-edited again, pull a fresh copy here
afterwards so the two don't silently drift apart — this file existing
at all was a gap until this session (unlike voicova-webhooks, which
already had a backup).

## What it does

Reverse-proxies voicova.com/www.voicova.com to the Railway origin,
answering `/robots.txt` and `/sitemap.xml` directly and rewriting the
HTML `<head>` (title/description/OG/canonical) server-side via
Cloudflare's `HTMLRewriter`, so crawlers see real metadata in the raw
HTML without needing to execute JavaScript. Full rationale and the
1 Sept 2026 WebSocket-Host-header fix are documented in the file's own
header comment — read that first, this README doesn't repeat it.

## Live deployment (confirmed 2 Sept 2026 via the Cloudflare API)

- **Worker name:** `voicova-seo`
- **Last modified:** 2026-09-01T13:03:23Z
- **Routes:** `voicova.com/*` and `www.voicova.com/*` → this Worker
  (confirmed by the presence of the full proxy + HTMLRewriter logic in
  the live code, not just the 4-static-path rollback state — the
  WebSocket issue was resolved by explicitly setting the outgoing
  `Host` header to match the public hostname, restoring full-domain
  proxying rather than staying narrowed to the 4 static SEO paths)
- **Env vars:** none — `ORIGIN` is hardcoded in the script
  (`web-production-8022c.up.railway.app`); update this constant and
  redeploy if the Railway origin's public hostname ever changes.

## Status as of 2 Sept 2026

Confirmed live and matching this file exactly (pulled via
`workers_get_worker_code`, byte-for-byte against this copy). No open
issues known against this Worker as of this session.
