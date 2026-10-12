# fitlog-mcp — Demo Video Script (< 3 min, English)

Target: public on YouTube/Vimeo, English narration, screen recording.
Total budget: ~2:40. All Alexa+ shots are the **official web simulator**
with the FitLog add-on deployed to the development stage — no Echo needed.

## Shot list & narration

**0:00–0:15 — Hook (simulator)**
Show: Alexa+ web simulator, empty conversation.
Say: "Meet FitLog — an Alexa+ add-on backed by a self-hosted MCP server that
gives Alexa a memory for your training life. Zero dependencies, just
Python's standard library."

**0:15–0:35 — It's really connected (terminal, quick)**
Show: `python3 server.py` → "listening on .../mcp (bearer auth required)";
`cloudflared tunnel` URL; `alexa-ai deploy` → dev stage.
Say: "One command starts the server. It's exposed over HTTPS, registered as
an Alexa+ add-on, and locked down with OAuth 2.1 plus PKCE — unauthenticated
requests get a 401, no exceptions."

**0:35–1:15 — Log a workout by voice (simulator)**
Show: type/say "Ask FitLog to log my bench press: 5 reps at 185 pounds."
Response shows the logged sets (auto-converted to kg) and a "New PR" callout.
Say: "Log sets by voice right after your last rep — pounds work too. The
server remembers every session and calls out personal records on its own.
No spreadsheet, no app to open."

**1:15–1:45 — Records + today's plan (simulator)**
Show: "Ask FitLog what my squat personal record is" → PR with estimated 1RM;
"Ask FitLog what today's workout plan is" → the day's split.
Say: "Ask for any record any time — with estimated one-rep maxes computed
across every set, not just your heaviest. And whatever day it is, it knows
the plan."

**1:45–2:05 — Protein (simulator)**
Show: "Ask FitLog to log 45 grams of protein" → "Total today: 45 g (target
160–190 g)."
Say: "Protein gets the same treatment — log it through the day and always
know where you stand against your target."

**2:05–2:40 — Under the hood (terminal + code, brief)**
Show: `curl` to `/mcp` without a token → `401` (note: no WWW-Authenticate
header — Alexa+ requires exactly that); `python3 -m unittest discover -s tests` → OK, 51
tests; a timed `tools/call` through the tunnel → under 500 ms.
Say: "Under the hood: hand-rolled Streamable HTTP on MCP spec 2025-11-25,
OAuth 2.1 with PKCE account linking, mandatory Origin validation, and a
full test suite — including the auth flow itself. Round trips stay under
Alexa's 500 millisecond budget. That's FitLog — your training, remembered."

## Recording notes
- 1080p screen capture; terminal font ≥18pt.
- Use a fresh DB (`FITLOG_DB=/tmp/demo.db`) so PRs trigger on camera.
- Deploy the add-on to the dev stage before recording; enable it in the
  simulator. Pre-link the account so the OAuth screen doesn't eat clock.
- Keep narration tight; each simulator turn should resolve in one shot.
