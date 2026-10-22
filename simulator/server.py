#!/usr/bin/env python3
"""Household Memory Bridge - simulator backend (stdlib only).

Serves the chat UI (index.html) and proxies every chat turn to the REAL
MCP server over Streamable HTTP (JSON-RPC 2.0, protocol 2025-11-25):

    POST /mcp  initialize -> notifications/initialized -> tools/call

Each chat turn returns the assistant reply plus the raw JSON-RPC request
and response frames, so the UI can display the actual wire traffic.

Endpoints
    GET  /             -> simulator UI
    GET  /api/status   -> upstream health + tool catalogue
    POST /api/chat     -> route a message to an MCP tool, return the reply
                          together with the recorded JSON-RPC frames

Run:  python3 server.py     (see README.md)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# -- configuration ---------------------------------------------------------
FITLOG_MCP_URL = os.environ.get(
    "FITLOG_MCP_URL", "http://127.0.0.1:8765").rstrip("/")
# Static bearer token. The MCP server compares it to FITLOG_API_TOKEN and
# grants it the full scope set (fitlog.read + fitlog.write).
FITLOG_TOKEN = (os.environ.get("FITLOG_TOKEN")
                or os.environ.get("FITLOG_API_TOKEN")
                or "devtoken")
SIM_HOST = os.environ.get("SIMULATOR_HOST", "127.0.0.1")
SIM_PORT = int(os.environ.get("SIMULATOR_PORT", "8080"))
PROTOCOL_VERSION = "2025-11-25"
CLIENT_ID = "household-memory-bridge-simulator"
CLIENT_VERSION = "1.0.0"

# ==================================================================== mcp client

def _http(method: str, url: str, body: bytes | None = None,
          headers: dict | None = None, timeout: int = 20):
    """One HTTP round trip -> (status, headers, raw_body)."""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers or {}), raw


def _parse_body(raw: bytes, content_type: str):
    """The MCP server may answer with JSON *or* an SSE stream
    (text/event-stream). Both carry the same JSON-RPC payload."""
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return None
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: int = 20):
    data = json.dumps(payload).encode("utf-8")
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    h.update(headers or {})
    status, resp_headers, raw = _http("POST", url, data, h, timeout)
    body = _parse_body(raw, resp_headers.get("Content-Type", ""))
    return status, resp_headers, body, raw


def _mcp_endpoint() -> str:
    return FITLOG_MCP_URL + "/mcp"


# ==================================================================== mcp client

class McpClient:
    """A tiny Streamable-HTTP MCP client.

    Keeps one Mcp-Session-Id alive and records every JSON-RPC frame so the
    UI can show the exact wire traffic."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.session_id: str | None = None
        self._rpc_id = 0
        self._lock = threading.Lock()
        self.tools: list = []

    # -- low level ---------------------------------------------------------
    def _headers(self, with_session: bool = True) -> dict:
        h = {
            "Authorization": "Bearer " + self.token,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if with_session and self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _next_id(self) -> int:
        with self._lock:
            self._rpc_id += 1
            return self._rpc_id

    def _call(self, method: str, params: dict | None = None,
              notification: bool = False) -> dict:
        """Perform one JSON-RPC call; returns the recorded frame."""
        req_obj: dict = {"jsonrpc": "2.0", "method": method}
        if not notification:
            req_obj["id"] = self._next_id()
        if params is not None:
            req_obj["params"] = params

        t0 = time.monotonic()
        try:
            status, headers, body, raw = _post_json(
                self.url, req_obj, self._headers())
            error = None
        except Exception as exc:                       # network failure
            status, headers, body, raw = 0, {}, None, b""
            error = "%s: %s" % (type(exc).__name__, exc)
        elapsed = round((time.monotonic() - t0) * 1000, 1)

        new_sid = headers.get("Mcp-Session-Id") or headers.get(
            "mcp-session-id")
        if new_sid:
            self.session_id = new_sid

        frame = {
            "method": method,
            "notification": notification,
            "request": req_obj,
            "request_headers": self._headers(),
            "status": status,
            "response_headers": {
                k: v for k, v in headers.items()
                if k.lower() in ("content-type", "mcp-session-id")
            },
            "response": body,
            "raw_body": (raw.decode("utf-8", "replace")[:4000]
                         if raw else ""),
            "elapsed_ms": elapsed,
        }
        if error:
            frame["error"] = error
        return frame

    # -- MCP lifecycle -----------------------------------------------------
    def initialize(self) -> list:
        """initialize + notifications/initialized; returns both frames."""
        frames = []
        frames.append(self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_ID, "version": CLIENT_VERSION},
        }))
        frames.append(self._call("notifications/initialized",
                                 notification=True))
        return frames

    def list_tools(self) -> dict:
        frame = self._call("tools/list", {})
        result = _frame_result(frame)
        self.tools = result.get("tools") or []
        return frame

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._call("tools/call",
                          {"name": name, "arguments": arguments})


# ==================================================================== router

# Who is speaking. The UI owns the roster: it starts with one default user
# and can add or rename family members at runtime, sending any user_id
# string plus the display name it is currently showing.
DEFAULT_USER_ID = "user1"
DEFAULT_USER_NAME = "User 1"


def _slugify(value) -> str:
    """Turn an arbitrary label into a safe, stable id token."""
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:64]


def _display_name(uid: str) -> str:
    """'user1' -> 'User 1', 'emma' -> 'Emma', 'emma-2' -> 'Emma 2'."""
    parts = []
    for chunk in uid.split("-"):
        if not chunk:
            continue
        m = re.match(r"^([a-z]+)(\d*)$", chunk)
        if m:
            word, num = m.group(1), m.group(2)
            parts.append(word[:1].upper() + word[1:])
            if num:
                parts.append(num)
        else:
            parts.append(chunk[:1].upper() + chunk[1:])
    if not parts:
        return DEFAULT_USER_NAME
    return " ".join(parts)


def resolve_user(user_id, user_name=None) -> dict:
    """Resolve the speaker of a turn into {id, name, visibility}.

    Any user_id string is accepted -- the roster lives in the browser, so
    the server only needs a safe id and a human-readable name to attribute
    saved facts to.
    """
    uid = _slugify(user_id) or DEFAULT_USER_ID
    name = str(user_name or "").strip()
    if not name:
        name = _display_name(uid)
    return {"id": uid, "name": name[:60], "visibility": "family"}


# Canned answers for the small talk the simulator handles locally.
_SMALLTALK = [
    (r"^\s*(hi|hey|hello|yo|good (morning|afternoon|evening))\b",
     "Hey! Ask me to remember or look something up for the family."),
    (r"\b(thank you|thanks)\b", "Anytime!"),
    (r"\b(who are you|what can you do)\b",
     "I'm the Household Memory Bridge. I can save family facts, look them "
     "up, and delete them."),
]

_CATEGORY_HINTS = [
    (r"\ballerg|medical|medicine|pill|doctor|health\b", "health"),
    (r"\bschool|teacher|class|homework|college|exam|study\b", "school"),
    (r"\bwork|job|office|meeting|boss|shift\b", "work"),
    (r"\bfood|meal|lunch|dinner|breakfast|recipe|vegetarian|vegan|"
     r"allergy|diet\b", "food"),
    (r"\btrip|travel|flight|hotel|vacation|holiday|visit\b", "travel"),
    (r"\bpet|dog|cat|vet|fish|hamster\b", "pets"),
    (r"\bkitchen|dish|chore|house|laundry|clean|grocery|shopping\b",
     "household"),
    (r"\bbirthday|anniversary|holiday|christmas|new year\b", "family"),
]


def _looks_like_delete(text: str) -> bool:
    """True when the message asks for a fact to be removed."""
    return bool(_DELETE_START.search(text)
                or re.search(r"\b(delete|remove|forget)\s+(?:the\s+)?"
                             r"(fact|note|memory|entry|item|#?\d+)\b",
                             text, re.I))


def _extract_fact_id(text: str):
    """Pull a fact id out of 'delete fact 3' / 'delete #3' / 'remove 3'."""
    m = re.search(r"(?:fact|#)\s*(\d+)", text, re.I)
    if not m:
        m = re.search(r"\b(\d+)\b", text)
    return int(m.group(1)) if m else None


# An imperative delete at the start of the message: "Delete fact 3".
_DELETE_START = re.compile(
    r"^\s*(?:please\s+|hey\s+|ok\s+|also\s+)*"
    r"(?:delete|remove|forget|erase|clear)\b", re.I)


_STOPWORDS = {
    "a", "about", "all", "an", "and", "any", "are", "do", "does", "do",
    "did", "for", "have", "i", "is", "it", "know", "me", "my", "of",
    "on", "please", "remember", "the", "to", "we", "what", "with", "you",
    "your", "us", "our", "they", "them", "he", "she", "his", "her",
    "whats", "whats", "was", "were", "there", "that", "this", "can",
    "tell", "search", "look", "find", "check", "recall", "give", "get",
}

_QUERY_WORDS = {
    "who", "whose", "when", "where", "which", "what", "why", "how",
    "food", "foods", "facts", "fact", "note", "notes", "news", "info",
    "information", "detail", "details", "stuff", "things", "thing",
}

# Family members: the subject of a question, rarely the topic of it.
_PERSON_WORDS = {
    "mom", "mum", "mother", "dad", "pa", "papa", "father",
    "emma", "sarah", "james", "noah", "olivia", "liam", "mia",
    "kids", "kid", "child", "children", "son", "daughter", "brother",
    "sister", "family", "everyone", "all", "we", "us", "our", "i",
}


def _stem(word: str) -> str:
    """Reduce a word to a form that is still a substring of every common
    inflection, so the store's LIKE '%query%' search can match it.

    'allergies' -> 'allerg'  (matches 'allergic' and 'allergy')
    'peanuts'   -> 'peanut'  (matches 'peanuts')
    'holiday'   -> 'holiday' (unchanged)
    """
    w = word.lower()
    w = re.sub(r"['\u2019]s$", "", w)      # possessive -> base form
    w = re.sub(r"['\u2019]$", "", w)
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3]                      # allergies -> allerg
    elif w.endswith("sses") or w.endswith("shes") or w.endswith("ches"):
        w = w[:-2]                      # dishes -> dish
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]                      # peanuts -> peanut
    elif w.endswith("y") and len(w) > 3 and w[-2] not in "aeiou":
        w = w[:-1]                      # allergy -> allerg
    return w


def _recall_query(text: str) -> str:
    """Pick the topic word out of a recall request and stem it.

    Person names ("Mom", "Dad", "Emma") are usually the *subject* of the
    question, not the topic being asked about -- "What allergies does Mom
    have?" is a search for "allergies". So prefer a non-person word when
    one is available.
    """
    words = [w for w in re.findall(r"[A-Za-z']+", text)]
    candidates = [w for w in words
                  if w.lower() not in _STOPWORDS
                  and w.lower() not in _QUERY_WORDS
                  and len(w) > 2]
    # Drop the speaker names so the topic word wins.
    topics = [w for w in candidates if w.lower() not in _PERSON_WORDS]
    if topics:
        candidates = topics
    if not candidates:
        candidates = [w for w in words if len(w) > 2]
    if not candidates:
        return _extract_query(text)
    # The longest word carries the most meaning.
    best = max(candidates, key=len)
    return _stem(best)


def _extract_query(text: str) -> str:
    """Strip the conversational wrapper off a recall request."""
    q = re.sub(r"^\s*(?:do you (?:remember|know)|can you (?:remember|know)|"
               r"what do you (?:remember|know) about|what do we know about|"
               r"do we know about|who knows about|"
               r"recall|search for|look up|find|check)\s*", "",
               text, flags=re.I).strip()
    q = re.sub(r"^\s*about\s+", "", q, flags=re.I).strip()
    q = re.sub(r"[?!.]+$", "", q).strip()
    return q or text.strip()


def _guess_category(text: str) -> str:
    low = text.lower()
    for pattern, cat in _CATEGORY_HINTS:
        if re.search(pattern, low):
            return cat
    return "family"


def _guess_visibility(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(just between us|private|only i|only me|don'?t tell|"
                 r"secret)\b", low):
        return "private"
    if re.search(r"\b(shared|family|everyone|all of us|everyone should)\b",
                 low):
        return "family"
    return "family"


def _extract_content(text: str) -> str:
    """Turn 'Remember that I am allergic to peanuts' into the fact body."""
    c = re.sub(r"^\s*(please\s+|hey\s+|ok\s+|also\s+)*"
               r"(remember|note|save|store|add|record)\s*", "", text,
               flags=re.I).strip()
    c = re.sub(r"^\s*(that\s+|this\s+|for\s+the\s+family\s+)?", "", c,
               flags=re.I).strip()
    c = re.sub(r"[.?!]+$", "", c).strip()
    return c or text.strip()


# Imperative "remember/note/save ..." at the start of a message means SAVE.
_SAVE_START = re.compile(
    r"^\s*(?:please\s+|hey\s+|ok\s+|also\s+)*"
    r"(?:remember|note|save|store|add|record)\b", re.I)

# Question forms that mean RECALL.
_RECALL_ANY = re.compile(
    r"\b(do you remember|do you know|can you remember|can you know|"
    r"what do you know|do we know|what do we know|"
    r"what do you remember|recall|search for|look up|find|check|"
    r"who|whose|when|where|which|what did|any (?:facts|notes|news))\b",
    re.I)

# A message that opens with an interrogative word is a question, and a
# question to the bridge means "tell me what you know" (recall).
_INTERROGATIVE_START = re.compile(
    r"^\s*(what|who|whom|whose|when|where|which|why|how|is|are|was|were|"
    r"do|does|did|can|could|have|has|any|tell me)\b", re.I)

# Explicit save phrasing anywhere in the sentence.
_SAVE_ANY = re.compile(
    r"\b(remember|note that|save|store|record|tell you|important|"
    r"fact that)\b", re.I)


def route(text: str, user_id: str, user_name: str | None = None) -> dict:
    """Map a chat message to a plan: {tool, arguments, reply}.

    'tool' is None for locally answered small talk."""
    user = resolve_user(user_id, user_name)
    low = text.lower()

    def save_plan():
        return {
            "tool": "save_household_fact",
            "arguments": {
                "content": _extract_content(text),
                "category": _guess_category(text),
                "visibility": _guess_visibility(text),
                "added_by": user["name"],
            },
            "reply": None,
        }

    # 1. delete
    if _looks_like_delete(text):
        fact_id = _extract_fact_id(text)
        if fact_id is None:
            return {
                "tool": None,
                "arguments": {},
                "reply": "Which fact should I delete? Tell me its number, "
                         "for example \"delete fact 3\".",
            }
        return {
            "tool": "forget_fact",
            "arguments": {"fact_id": fact_id},
            "reply": None,
        }

    # 2. imperative save ("Remember that Emma is allergic to peanuts")
    if _SAVE_START.search(text):
        return save_plan()

    # 3. small talk (checked before recall: "who are you" is not a query)
    for pattern, reply in _SMALLTALK:
        if re.search(pattern, low):
            return {"tool": None, "arguments": {}, "reply": reply}

    # 4. recall ("What do you remember about allergies?")
    if _RECALL_ANY.search(low):
        return {
            "tool": "recall_fact",
            "arguments": {"query": _recall_query(text)},
            "reply": None,
        }

    # 4b. any other question ("What allergies does Mom have?") is a recall.
    if _INTERROGATIVE_START.search(text):
        return {
            "tool": "recall_fact",
            "arguments": {"query": _recall_query(text)},
            "reply": None,
        }

    # 5. save phrasing anywhere
    if _SAVE_ANY.search(low):
        return save_plan()

    # 6. fallback: treat it as a fact to remember
    return save_plan()


# ==================================================================== replies

def _tool_reply(tool: str, text: str, is_error: bool) -> str:
    """Turn the raw tool output into something a person would say."""
    if is_error:
        return text or "Sorry, that didn't work."
    if tool == "save_household_fact":
        return "Got it - I've saved that for the family. " + text
    if tool == "forget_fact":
        return "Done. " + text
    if tool == "recall_fact":
        if text.lower().startswith("no facts found"):
            return "I couldn't find anything matching that."
        return "Here's what I remember:\n\n" + text
    return text


def _extract_text(response) -> tuple:
    """Pull (text, isError) out of a tools/call JSON-RPC response."""
    if not isinstance(response, dict):
        return "", True
    result = response.get("result")
    if not isinstance(result, dict):
        return "", True
    parts = result.get("content") or []
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return text, bool(result.get("isError"))


def _frame_result(frame: dict) -> dict:
    """Safely pull the JSON-RPC 'result' object out of a recorded frame.

    The body may be missing (notification), an HTTP-level error object, or
    something entirely unexpected -- never assume it is a dict.
    """
    response = frame.get("response")
    if not isinstance(response, dict):
        return {}
    result = response.get("result")
    return result if isinstance(result, dict) else {}


def _rpc_error(response) -> str:
    """Describe a JSON-RPC error, an HTTP-level error body, or nothing.

    The MCP server answers an unauthenticated request with HTTP 401 and a
    body like {"error": "invalid_token", "error_description": "..."} where
    'error' is a *string*, while a JSON-RPC failure uses an object
    {"code": -32601, "message": "..."}. Handle both.
    """
    if not isinstance(response, dict):
        return ""
    err = response.get("error")
    if not err:
        return ""
    if isinstance(err, dict):
        detail = err.get("message", "unknown error")
        code = err.get("code")
        return "%s (%s)" % (detail, code if code is not None else "?")
    # HTTP-level error: {"error": "invalid_token", "error_description": ...}
    desc = response.get("error_description")
    return "%s%s" % (err, (": " + desc) if desc else "")


# ==================================================================== chat

def handle_chat(message: str, user_id: str, history: list,
                user_name: str | None = None) -> dict:
    """Run one chat turn end to end and return the UI payload."""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "empty message", "frames": []}

    user = resolve_user(user_id, user_name)
    user_id = user["id"]

    client = McpClient(_mcp_endpoint(), FITLOG_TOKEN)
    frames = []

    # 1. handshake
    frames.extend(client.initialize())
    init_resp = frames[0].get("response")
    if not isinstance(init_resp, dict):
        init_resp = {}
    if frames[0].get("error"):
        return {
            "ok": False,
            "error": "Could not reach the MCP server: "
                     + frames[0]["error"],
            "frames": frames,
        }
    if "error" in init_resp or frames[0].get("status", 0) >= 400:
        return {
            "ok": False,
            "error": "MCP initialize failed: "
                     + (_rpc_error(init_resp)
                        or "HTTP %s" % frames[0].get("status")),
            "frames": frames,
        }

    # 2. discover the tools
    tools_frame = client.list_tools()
    frames.append(tools_frame)
    if tools_frame.get("error"):
        return {
            "ok": False,
            "error": "Could not reach the MCP server: "
                     + tools_frame["error"],
            "frames": frames,
        }
    tool_names = [t.get("name") for t in client.tools if isinstance(t, dict)]
    if not tool_names:
        return {
            "ok": False,
            "error": "MCP server returned no tools",
            "frames": frames,
        }

    # 3. decide what to call
    plan = route(message, user_id, user_name)
    tool = plan["tool"]
    arguments = plan["arguments"]
    reply = plan["reply"]
    tool_used = None

    if tool:
        if tool not in tool_names:
            return {
                "ok": False,
                "error": "tool %r is not exposed by the MCP server" % tool,
                "frames": frames,
            }
        call_frame = client.call_tool(tool, arguments)
        frames.append(call_frame)
        tool_used = tool
        rpc_err = _rpc_error(call_frame.get("response"))
        if rpc_err:
            reply = "The MCP server rejected that: " + rpc_err
        elif call_frame.get("error"):
            reply = "Could not reach the MCP server: " + call_frame["error"]
        else:
            text, is_error = _extract_text(call_frame.get("response"))
            reply = _tool_reply(tool, text, is_error)

    return {
        "ok": True,
        "reply": reply,
        "tool": tool_used,
        "arguments": arguments,
        "user_id": user_id,
        "frames": frames,
    }


# ==================================================================== http

class SimulatorHandler(BaseHTTPRequestHandler):
    server_version = "HouseholdMemoryBridgeSim/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers -----------------------------------------------------------
    def log_message(self, fmt, *args):
        sys.stderr.write("[sim] %s - %s\n"
                         % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_html(self, status: int, html: str):
        self._send(status, html.encode("utf-8"),
                   "text/html; charset=utf-8")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            return self._serve_index()
        if path == "/api/status":
            return self._serve_status()
        if path == "/api/health":
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/api/chat":
            return self._serve_chat()
        self._send_json(404, {"ok": False, "error": "not found"})

    # -- handlers ----------------------------------------------------------
    def _serve_index(self):
        index_path = os.path.join(HERE, "index.html")
        try:
            with open(index_path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send_html(500, "<h1>index.html not found</h1>")
        self._send(200, body, "text/html; charset=utf-8")

    def _serve_status(self):
        """Handshake with the MCP server and list its tools."""
        client = McpClient(_mcp_endpoint(), FITLOG_TOKEN)
        try:
            init = client.initialize()
        except Exception as exc:
            return self._send_json(200, {
                "ok": False,
                "upstream": FITLOG_MCP_URL,
                "error": "%s: %s" % (type(exc).__name__, exc),
            })
        init_resp = init[0].get("response")
        if not isinstance(init_resp, dict):
            init_resp = {}
        if init[0].get("error"):
            return self._send_json(200, {
                "ok": False,
                "upstream": FITLOG_MCP_URL,
                "error": "could not reach the MCP server: "
                         + init[0]["error"],
            })
        if "error" in init_resp or init[0].get("status", 0) >= 400:
            return self._send_json(200, {
                "ok": False,
                "upstream": FITLOG_MCP_URL,
                "error": "initialize failed: "
                         + (_rpc_error(init_resp)
                            or "HTTP %s" % init[0].get("status")),
            })
        frame = client.list_tools()
        result = _frame_result(frame)
        tools = result.get("tools") or []
        if frame.get("error"):
            return self._send_json(200, {
                "ok": False,
                "upstream": FITLOG_MCP_URL,
                "error": "could not reach the MCP server: " + frame["error"],
            })
        if not tools:
            return self._send_json(200, {
                "ok": False,
                "upstream": FITLOG_MCP_URL,
                "error": "initialize failed: "
                         + (_rpc_error(frame.get("response"))
                            or "HTTP %s" % frame.get("status")),
            })
        self._send_json(200, {
            "ok": bool(tools),
            "upstream": FITLOG_MCP_URL,
            "endpoint": _mcp_endpoint(),
            "protocol_version": PROTOCOL_VERSION,
            "status": frame.get("status"),
            "tools": [{"name": t.get("name"),
                       "description": t.get("description"),
                       "inputSchema": t.get("inputSchema")}
                      for t in tools if isinstance(t, dict)],
        })

    def _serve_chat(self):
        data = self._read_json()
        if data is None:
            return self._send_json(400, {"ok": False,
                                         "error": "invalid JSON body"})
        message = str(data.get("message") or "")
        user_id = str(data.get("user_id") or DEFAULT_USER_ID)
        user_name = str(data.get("user_name") or "").strip() or None
        history = data.get("history") or []
        if not isinstance(history, list):
            history = []
        try:
            result = handle_chat(message, user_id, history, user_name)
        except Exception as exc:
            return self._send_json(500, {
                "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "frames": [],
            })
        self._send_json(200, result)


def main():
    httpd = ThreadingHTTPServer((SIM_HOST, SIM_PORT), SimulatorHandler)
    print("Household Memory Bridge simulator on http://%s:%d"
          % (SIM_HOST, SIM_PORT), flush=True)
    print("  upstream MCP: %s/mcp" % FITLOG_MCP_URL, flush=True)
    print("  bearer token: %s" % ("set" if FITLOG_TOKEN else "MISSING"),
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
