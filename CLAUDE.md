# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Anti-bot fingerprint lab — a learning project that demonstrates how websites detect
automation and how those signals can be replicated. Two layers:

- **Layer 1 (lab1–4): "Getting past the wall"** — TLS fingerprinting, browser fingerprinting,
  local judge server, Turnstile decoding.
- **Layer 2 (lab5): "Extracting content"** — multi-strategy corresponding-author email
  extraction from academic article pages.

## Environment

- Python venv: `C:\Users\hao.guo\.workbuddy\binaries\python\envs\default`
- Dependencies: `curl_cffi`, `playwright`, `playwright-stealth`, `patchright`, `camoufox`, `lxml`
- All `.cmd` launchers bind to this venv + set UTF-8. Use them, not bare `python`.

## Running

```
lab                              # Full suite with defaults (lab1→tls.peet.ws, lab2→example.com, lab3→local judge, lab4→demo test key)
lab https://yoursite.com/        # Full suite hitting your site (lab1 still uses tls.peet.ws)
lab --headed https://yoursite.com/  # lab3/lab4 with visible browser windows
lab5 https://yoursite.com/article  # Single article email extraction
lab5 --list urls.txt --headed      # Batch extraction with resume support
lab5 --list urls.txt --headed --delay 5-10 --rest-every 10:30  # Batch with IP-warming delays
```

Individual labs: `lab4.cmd` (Turnstile only), `lab5.cmd` (extraction only), or run the
`.py` files directly via the venv python.

## Key files

| File | Purpose |
|---|---|
| `lab.py` | Unified entry point, dispatches to lab1–4 |
| `lab1_tls_fingerprint.py` | TLS/JA3/HTTP2 fingerprint comparison (urllib vs requests vs curl_cffi) |
| `lab2_browser_fingerprint.py` | Navigator/WebGL/plugin leak detection (vanilla vs stealth) |
| `lab3_local_judge.py` | 6-client matrix against a target site + local judge server |
| `lab4_turnstile.py` | Turnstile widget token + edge clearance (`cf_clearance`) flows |
| `lab5_extract.py` | Multi-strategy email extraction + ✉ click flow + batch mode |
| `judge_server.py` | Local HTTP server on :8765 that scores requests as bot/human |
| `lab5_results.csv` | Batch extraction output (`article_url, title, authors, email, source`) |

## Lab4 Turnstile — critical distinction

Two completely separate flows, don't mix them up:

- **Widget token** (`cf-turnstile-response`): From an embedded form widget with a site key.
  Verified by your backend via siteverify. Easier path.
- **Edge clearance** (`cf_clearance`): From a full-page interstitial challenge (`cf-mitigated: challenge`).
  Cookie bound to IP+UA+Chrome fingerprint. Harder, but can be reused in curl_cffi
  (same IP, same UA, chrome146 TLS profile) for ~30min.

Production path for edge walls: headed patchright → human clicks checkbox →
`cf_clearance` cookie → move to curl_cffi for bulk requests. Demo uses Cloudflare's
official test sitekey (`1x00000000000000000000AA`) which skips real challenges.

## Lab5 extraction strategies

Runs all strategies (no short-circuit), unions results, deduplicates:

1. `citation_*` meta tags
2. JSON-LD (`in_author` propagation, mailto:/%40 normalization)
3. JS `String.fromCharCode` decoding
4. mailto: links (single = corresponding by default; multiple = only in correspondence section)
5. CF email obfuscation (`data-cfemail` + `/cdn-cgi/...#hex`, class-context for corresponding)
6. Correspondence section (anchored on "Corresponding author" text, excludes "correspondence analysis")
7. at-dot patterns (confirm-only, don't add new candidates)
8. Reversed attributes
9. Script-embedded emails
10. Dynamic hunt: networkidle + XHR intercept + reveal buttons (only when no strong signal found)
11. ✉ click flow: finds envelope/mail icons, clicks each, accumulates emails across popups

Batch mode (`--list`): one browser session, cf_clearance reused, per-article delays,
resume via `lab5_results.csv` (skip already-processed URLs).

## Key constraints

- **Only use on your own sites.** The Turnstile auto-click and ✉ flow are for learning.
- `lab1` always hits `tls.peet.ws` regardless of target URL — it needs a TLS echo service.
- `cf_clearance` is bound to IP+UA+Chrome fingerprint; can't move across machines/IPs.
- Extraction ceiling: can only get data that the page actually delivers. Domain-locked JS
  libraries that abort prevent email rendering entirely (Path A: fix the site, not the scraper).
