"""JSON-RPC 2.0 dispatch and MCP session lifecycle.

Implements the initialize handshake, protocol-version negotiation bookkeeping,
and dispatch for ping / tools / resources. Transport concerns (HTTP status
codes, headers, Origin validation) live in transport.py.
"""

from __future__ import annotations

import secrets
import threading
import time

from mcp import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION
from mcp import tools as tool_defs

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class SessionStore:
    """In-memory MCP sessions, keyed by Mcp-Session-Id."""

    def __init__(self, ttl_seconds: int = 1800):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.ttl = ttl_seconds

    def create(self) -> str:
        sid = secrets.token_hex(16)
        with self._lock:
            self._prune_locked()
            self._sessions[sid] = {
                "protocol_version": PROTOCOL_VERSION,
                "created_at": time.time(),
                "last_seen": time.time(),
                "initialized": False,
            }
        return sid

    def get(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return None
            if time.time() - s["last_seen"] > self.ttl:
                del self._sessions[sid]
                return None
            s["last_seen"] = time.time()
            return s

    def mark_initialized(self, sid: str) -> None:
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid]["initialized"] = True

    def destroy(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s["last_seen"] > self.ttl
        ]
        for sid in expired:
            del self._sessions[sid]


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _initialize_result():
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_rpc(msg: dict, ctx, scopes: set[str] | None = None) -> dict | None:
    """Dispatch one JSON-RPC message. Returns a response dict, or None for
    notifications (transport should answer 202 with no body).

    `scopes` is the granted scope set of the bearer token that authorized
    this request (None = unauthenticated; the transport rejects those
    before we get here). tools/call is checked against TOOL_SCOPES.
    """
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, INVALID_REQUEST, "Invalid JSON-RPC 2.0 message.")
    method = msg.get("method")
    if not isinstance(method, str):
        return _err(msg.get("id"), INVALID_REQUEST, "Missing 'method'.")
    msg_id = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        # e.g. "params": [...] -- a client bug, not an internal error.
        return _err(msg_id, INVALID_PARAMS, "'params' must be an object.")

    if method == "initialize":
        # Version negotiation: we speak 2025-11-25; echo it back.
        return _ok(msg_id, _initialize_result())

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": tool_defs.TOOL_DEFINITIONS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _err(msg_id, INVALID_PARAMS, "Missing 'name' for tools/call.")
        if not isinstance(arguments, dict):
            return _err(msg_id, INVALID_PARAMS, "'arguments' must be an object.")
        required = tool_defs.TOOL_SCOPES.get(name)
        if required is not None:
            # Known tool: enforce the scope. Unknown names fall through to
            # call_tool, which reports them as a tool-level isError result.
            if scopes is None or required not in scopes:
                return _err(
                    msg_id, INVALID_PARAMS,
                    f"Insufficient scope: '{name}' requires '{required}'.",
                )
        text, is_error = tool_defs.call_tool(ctx.store, name, arguments)
        return _ok(
            msg_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )

    if method == "resources/list":
        # No static resources are defined for the household-memory model.
        return _ok(msg_id, {"resources": []})

    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str):
            return _err(msg_id, INVALID_PARAMS, "Missing 'uri' for resources/read.")
        return _err(msg_id, INVALID_PARAMS, f"Unknown resource: {uri}")

    if is_notification:
        # Unknown notifications are ignored per JSON-RPC.
        return None
    return _err(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
