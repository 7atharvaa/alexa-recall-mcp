# fitlog-mcp

An Alexa+ add-on backed by a self-hosted MCP server that gives Alexa a
memory for your training: log sets by voice, ask for personal records, get
today's plan and protein targets.

Built for the Alexa+ track of the Amazon **"Build, Ship, Shape" Developer
Hackathon 2026**: a working self-hosted MCP server implementing **MCP spec
2025-11-25** over **Streamable HTTP** with the **Alexa+ authentication
checklist** (OAuth 2.1 + PKCE account linking, 401s with no
`WWW-Authenticate` header, RFC 9728 Protected Resource Metadata) — zero
dependencies, MIT licensed.

## Run it (3 steps, no `pip install`)

Requires Python 3.9+.

```bash
git clone https://github.com/hahahahahahahahah6/fitlog-mcp.git
cd fitlog-mcp
python3 server.py
```

The server listens on `http://127.0.0.1:8765` (`/mcp` endpoint).
Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FITLOG_PORT` | `8765` | listen port |
| `FITLOG_HOST` | `127.0.0.1` | listen address |
| `FITLOG_DB` | `~/.fitlog/fitlog.db` | sqlite database path |
| `FITLOG_API_TOKEN` | _(empty)_ | static bearer token for local dev / Inspector |
| `FITLOG_PUBLIC_URL` | _(empty)_ | public HTTPS URL (e.g. cloudflared tunnel); used for OAuth metadata |
| `FITLOG_OWNER_PASSWORD` | _(empty)_ | owner password for the `/authorize` page. **Required** when `FITLOG_PUBLIC_URL` is set — the server refuses to start publicly without it |
| `FITLOG_ALLOWED_ORIGINS` | _(empty)_ | extra trusted `Origin` values, comma-separated |

## Connect an MCP client

The endpoint is `http://127.0.0.1:8765/mcp` (Streamable HTTP). Every `/mcp`
request needs a bearer token — either from the OAuth flow or the static
`FITLOG_API_TOKEN` you set for local dev. Example client config
(Claude Code / MCP Inspector style):

```json
{
  "mcpServers": {
    "fitlog": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": { "Authorization": "Bearer <FITLOG_API_TOKEN>" }
    }
  }
}
```

Or verify with the MCP Inspector CLI:

```bash
FITLOG_API_TOKEN=devtoken python3 server.py &
npx -y @modelcontextprotocol/inspector --cli \
  --transport http --server-url http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer devtoken" \
  --method tools/list
```

## Connect Alexa+ (official path)

1. Expose the server over HTTPS, e.g. with a cloudflared quick tunnel:
   `cloudflared tunnel --url http://127.0.0.1:8765`, and set
   `FITLOG_PUBLIC_URL=https://<your-tunnel>.trycloudflare.com`.
   Also set `FITLOG_OWNER_PASSWORD` to a strong password — public exposure
   without it is refused at startup, and the `/authorize` page will ask for
   the password before showing the Approve button.
2. Install the Alexa AI CLI (`npm install -g @alexa/alexa-ai` or per the
   [MCP toolkit quickstart](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-quickstart.html)).
3. Put your tunnel URL into `addon-package/addon.json`
   (`integrations[0].config.endpoints.default.uri`, plus `/mcp`).
4. `cd addon-package && alexa-ai deploy` — deploys to the development stage.
5. Enable the add-on in the Alexa+ **web simulator**, link the account
   (OAuth 2.1 + PKCE flow against your server), then talk to it:
   _"Ask FitLog to log my bench press: 5 reps at 185 pounds."_

The demo video script ([demo/demo_script.md](demo/demo_script.md)) follows
exactly this path.

No Alexa developer account? Use the bundled **web simulator** instead
([simulator/](simulator/)): an Echo-style page that runs the same OAuth
account-linking flow and calls the same 6 tools over Streamable HTTP, with
a live JSON-RPC inspector. `./simulator/start.sh`, then open
http://127.0.0.1:8799/.

## Tools

| Tool | What it does |
|---|---|
| `log_workout(exercise, sets[{reps, weight}], date?, unit?)` | Record a session; announces new PRs. `unit`: `"kg"` (default) or `"lb"` (auto-converted to kg) |
| `get_history(exercise?, limit?)` | Past sessions, newest first |
| `get_personal_records()` | Heaviest set + best estimated 1RM per lift |
| `plan_workout(day?)` | Today's plan from the built-in 5-day split |
| `log_protein(grams, date?)` | Log protein toward the 160–190 g target |
| `get_nutrition_targets()` | Targets vs. what's logged today |

Resources: `program://current` (the training split), `pr://all` (PR table).

## Spec compliance notes

- Streamable HTTP: `POST /mcp` for JSON-RPC, optional `GET /mcp` SSE stream,
  `DELETE /mcp` terminates the session (`Mcp-Session-Id`).
- **Alexa+ auth checklist**: OAuth 2.1 authorization-code + PKCE (S256);
  `/.well-known/oauth-protected-resource` (RFC 9728) and
  `/.well-known/oauth-authorization-server` metadata; authorize and token
  requests must carry the canonical MCP `resource` parameter. Unauthenticated
  requests to `/mcp` get HTTP **401 with no `WWW-Authenticate` header**, as
  required.
- `Origin` header is **MUST**-validated: a present-but-untrusted origin gets
  HTTP 403 (DNS-rebinding mitigation). Loopback origins are trusted by default.
- `MCP-Protocol-Version` is checked on every post-initialize request:
  unsupported versions get HTTP 400.
- JSON-RPC **batches are rejected** (removed in spec 2025-11-25) with
  `-32600` over HTTP 400.
- Tool annotations (`readOnlyHint` / `destructiveHint` / `idempotentHint` /
  `openWorldHint`) are set per the 2025-11-25 spec.

## Tests

```bash
python3 -m unittest discover -s tests   # 51 tests, incl. the full OAuth PKCE round-trip
```

## License

MIT — see [LICENSE](LICENSE).
