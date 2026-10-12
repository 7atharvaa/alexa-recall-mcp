# Devpost submission — FitLog MCP
### Build, Ship, Shape: Amazon Developer Hackathon (Alexa+ track)
### Deadline: 2026-10-23 12:00 PM PDT — DO NOT SUBMIT YET (needs browser login as haoli5933)

## Title
FitLog — Give Alexa+ a Memory for Your Training

## Tagline
Talk to Alexa+ mid-workout: it logs your sets, calls out PRs, and tracks your protein — no phone, no spreadsheet.

## Detailed description (Devpost "About" — follows Devpost's story prompts)

**Inspiration.** I lift five days a week and eat 160–190 g of protein a day. Mid-workout, logging sets on a phone is the last thing I want to do — chalky hands, short rest timers, zero patience. I wanted to just say the words and have it remembered. So I gave Alexa+ a memory for my training.

**What it does.** FitLog is a self-hosted MCP server that plugs into Alexa+ as a custom add-on. Six tools cover the full training loop:

- 🎙️ **Log by voice** — *"log my bench press: 3 sets of 5 at 185 pounds"* → sets recorded, pounds auto-converted, new PRs called out on the spot
- 🏆 **Personal records** — heaviest set per lift plus estimated 1RM computed across every set, not just the top one
- 📋 **Today's plan** — the session from a built-in 5-day split, whatever day it is
- 📜 **History** — recent sessions per exercise
- 🥩 **Protein** — *"log 45 grams of protein"* against the daily target, always knowing where you stand
- 🎯 **Targets** — daily protein and calorie goals

Two MCP resources (`program://current`, `pr://all`) expose the training program and the all-time PR table to any client.

**How I built it.** One Python process, **stdlib only** — `http.server`, `sqlite3`, `json`. No `pip install`, no supply-chain risk, runs anywhere Python 3.9+ exists. A single `/mcp` endpoint speaks **Streamable HTTP per MCP spec 2025-11-25**: `Mcp-Session-Id` lifecycle, mandatory `Origin` validation (DNS-rebinding mitigation), protocol-version checks, and JSON-RPC batches rejected per the spec. Auth follows the Alexa+ checklist to the letter: **OAuth 2.1 authorization-code + PKCE (S256)**, RFC 9728 Protected Resource Metadata, the canonical `resource` parameter on authorize/token requests, and 401s with **no `WWW-Authenticate` header** — exactly as Alexa+ requires. Account linking is the genuine flow, not a mock. **51/51 tests pass**, including the full PKCE round-trip.

**Challenges I ran into.** The official Alexa+ path needs an AWS assume-role that a brand-new developer account can't complete without Solutions Architect onboarding — so I pivoted to a self-hosted deployment over a stable public tunnel and built an Echo-style web simulator that runs the *real* OAuth dance and the *real* tools, so judges can try everything without an Echo or an AWS account. Hand-rolling Streamable HTTP without an SDK meant reading the spec line by line — the 401-without-`WWW-Authenticate` detail alone took a full debugging session. And every tool call had to land under Alexa's 500 ms round-trip budget over the tunnel; it does.

**Try it live.** The demo runs at a fixed public URL right now:

- 🎙️ **Live demo (Echo-style web simulator):** https://app.haocoach.com/
- 🔌 **MCP endpoint:** https://mcp.haocoach.com/mcp

Click "Link FitLog account" to run the actual OAuth flow, then type *"log my bench press: 3 sets of 5 at 185 pounds"* and expand the inspector to watch the raw `tools/call` JSON-RPC. The 54-second demo video (`demo/fitlog-demo-54s.mp4`) shows the whole loop.

**What's next.** The Alexa+ add-on package (`addon-package/addon.json`) is ready for the dev stage and points at the stable public endpoint. Next: rest-timer intents ("start a 90-second rest"), weekly volume analytics, and a bodyweight-trend tool — all through the same voice interface.

## Links
- GitHub: https://github.com/hahahahahahahahah6/fitlog-mcp (MIT)
- Live demo: https://app.haocoach.com/
- MCP server: https://mcp.haocoach.com/mcp

## Submission checklist (for the person clicking Submit in the browser)
- [ ] Log in to Devpost as `haoli5933` (registered for this hackathon on 2026-09-30 — confirmed)
- [ ] Create submission under Build, Ship, Shape → title/tagline/description above
- [ ] Demo URL: https://app.haocoach.com/
- [ ] Code repo: https://github.com/hahahahahahahahah6/fitlog-mcp
- [ ] Upload demo video: `demo/fitlog-demo-54s.mp4` (verified 54.0s, valid; 2026-10-11 decision: NOT re-recording — nice-to-have only, needs live-browser screen capture + narration)
- [x] Judge access decision (2026-10-11): do NOT give judges the owner password (it gates his personal training data — violates his no-production-password rule). Judges evaluate via demo video + public simulator + code. If hands-on linking is required, spin up an isolated demo instance with a throwaway password (needs his nod first).
- [ ] Fill hackathon's required questions (built with: Alexa+ MCP toolkit; track: Alexa+)
- [ ] Submit before 2026-10-23 12:00 PM PDT
