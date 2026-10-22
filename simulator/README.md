# Household Memory Bridge — web simulator

A web page that simulates the household assistant experience against the
**real** self-hosted FitLog MCP server — the route the hackathon rules
explicitly allow ("simulate an Alexa+ experience using your preferred
agentic tools via a web app").

What it does:

- **Chat UI** with a family-member switcher, conversation transcript and a
  dark, minimal look. The roster starts with a single default user
  (*User 1*, `user_id: user1`); you can add more members with the
  **Add family member** field and rename the active one by clicking its
  name.
- Every chat turn is routed by a small regex NLU to one of the 3 FitLog
  tools and executed as a real MCP `tools/call` (Streamable HTTP, bearer
  token, session id).
- The right-hand **raw traffic inspector** shows the exact JSON-RPC frames
  that went over the wire — `initialize` → `notifications/initialized` →
  `tools/list` → `tools/call` — with request/response headers, status codes
  and timings. Proof of a working MCP integration, on camera.

## Run it

Requires Python 3.9+, no `pip install`. Two terminals.

**Terminal 1 — the MCP server** (fresh DB so the demo starts clean):

```bash
cd ~/workspace/fitlog-mcp   # or wherever you cloned the repo
FITLOG_API_TOKEN='devtoken' \
FITLOG_DB=/tmp/fitlog-demo.db \
python3 server.py
# -> fitlog-mcp listening on http://127.0.0.1:8765/mcp
```

**Terminal 2 — the simulator:**

```bash
cd ~/workspace/fitlog-mcp/simulator
./start.sh
# -> Household Memory Bridge simulator on http://127.0.0.1:8080/
```

Then open http://127.0.0.1:8080/ in a browser.

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FITLOG_MCP_URL` | `http://127.0.0.1:8765` | FitLog MCP server base URL (no trailing `/mcp`) |
| `FITLOG_TOKEN` / `FITLOG_API_TOKEN` | `devtoken` | bearer token sent to the MCP server |
| `SIMULATOR_HOST` | `127.0.0.1` | interface the simulator listens on |
| `SIMULATOR_PORT` | `8080` | simulator port |

The simulator never holds the owner password — it only uses the same static
bearer token any other MCP client would use.

## Demo click path (for the video)

1. Browser: http://127.0.0.1:8080/
2. The header shows **MCP connected — 3 tools** once the simulator has
   completed a real `initialize` + `tools/list` handshake.
3. Type a message (there are no hint buttons — just type):
   - “Remember that Emma is allergic to peanuts” → `save_household_fact`.
   - “What do you remember about allergies?” → `recall_fact`.
   - “What allergies does Mom have?” → `recall_fact` (any question is a
     recall; the query word is stemmed, so *allergies* finds *allergic*).
   - “Delete fact 1” → `forget_fact`.
   - “Hi” → answered locally, no MCP call.
4. The traffic panel on the right shows every JSON-RPC frame with headers.
5. To demo several speakers: type a name into **Add family member** and
   press the `+` button, then switch between the pills. Each member keeps
   their own conversation history, and facts they save are attributed to
   their display name. Click the **active** member's name to rename it.

Routing is a small regex NLU, in priority order: delete → imperative save
(“Remember that…”) → small talk → recall question → save phrasing → default
save. Anything that starts with an interrogative word (*what, who, when,
where, which, how*) is treated as a recall.

If the MCP server is unreachable or the bearer token is rejected, the UI shows
the real reason (transport error, or the server's `invalid_token` body)
instead of a generic failure, and the traffic panel still shows the frames
that did go out.

## HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | the simulator UI |
| `/api/status` | GET | upstream health + the live tool catalogue |
| `/api/health` | GET | liveness probe |
| `/api/chat` | POST | `{message, user_id, user_name, history}` → `{ok, reply, tool, arguments, frames}` |

`user_id` is any stable id string (the UI generates one per family member);
`user_name` is the display name the fact should be attributed to and is
optional. `frames` is the full recorded JSON-RPC traffic for the turn.

## Files

- `server.py` — stdlib-only backend: static UI, MCP client
  (initialize → notifications/initialized → tools/list → tools/call),
  regex NLU router, frame recorder.
- `index.html` — the chat UI (vanilla HTML/CSS/JS, no build step).
- `start.sh` — one-command launcher.
