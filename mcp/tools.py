"""MCP tool definitions, annotations, and handlers for fitlog-mcp.

Tool annotations follow MCP spec 2025-11-25 (readOnlyHint / destructiveHint /
idempotentHint / openWorldHint).
"""

from __future__ import annotations

from store import Store

# Scope required to call each tool. Read-only tools need fitlog.read;
# tools that write data need fitlog.write.
TOOL_SCOPES = {
    "save_household_fact": "fitlog.write",
    "recall_fact": "fitlog.read",
    "forget_fact": "fitlog.write",
}


# ---------------------------------------------------------------- handlers

def _h_save_household_fact(store: Store, args: dict) -> tuple[str, bool]:
    content = args.get("content")
    if not content or not isinstance(content, str) or not content.strip():
        return "Missing required argument: 'content' (string).", True

    category = args.get("category")
    if not category or not isinstance(category, str) or not category.strip():
        return "Missing required argument: 'category' (string).", True

    visibility = args.get("visibility")
    if visibility not in ("private", "family"):
        return "Missing or invalid 'visibility'; must be 'private' or 'family'.", True

    added_by = args.get("added_by")
    if not added_by or not isinstance(added_by, str) or not added_by.strip():
        return "Missing required argument: 'added_by' (string).", True

    fact_id = store.save_fact(content, category, visibility, added_by)
    return f"Saved fact #{fact_id}: {content}", False


def _h_recall_fact(store: Store, args: dict) -> tuple[str, bool]:
    query = args.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return "Missing required argument: 'query' (string).", True

    facts = store.recall_fact(query)
    if not facts:
        return f"No facts found matching '{query}'.", False

    lines = [
        f"#{f['id']} [{f['category']}] ({f['visibility']}) by {f['added_by']}: "
        f"{f['content']}"
        for f in facts
    ]
    return "\n".join(lines), False


def _h_forget_fact(store: Store, args: dict) -> tuple[str, bool]:
    fact_id = args.get("fact_id")
    if fact_id is None:
        return "Missing required argument: 'fact_id' (integer).", True
    try:
        fact_id = int(fact_id)
    except (TypeError, ValueError):
        return "'fact_id' must be an integer.", True
    deleted = store.forget_fact(fact_id)
    if deleted == 0:
        return f"No fact found with id #{fact_id}.", True
    return f"Deleted fact #{fact_id}.", False


_HANDLERS = {
    "save_household_fact": _h_save_household_fact,
    "recall_fact": _h_recall_fact,
    "forget_fact": _h_forget_fact,
}


def call_tool(store: Store, name: str, arguments: dict | None) -> tuple[str, bool]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True
    return handler(store, arguments or {})


# ---------------------------------------------------------------- definitions

TOOL_DEFINITIONS = [
    {
        "name": "save_household_fact",
        "description": (
            "Save a household memory/fact with a category, visibility, and author. "
            "Visibility is 'private' (only the adding user) or 'family' (shared "
            "with family)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact or memory to store.",
                },
                "category": {
                    "type": "string",
                    "description": "Category for grouping facts (e.g. 'family', 'preferences').",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "family"],
                    "description": "Whether the fact is private or shared with family.",
                },
                "added_by": {
                    "type": "string",
                    "description": "Who is adding the fact.",
                },
            },
            "required": ["content", "category", "visibility", "added_by"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "recall_fact",
        "description": "Search for stored facts matching a natural-language query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query; matches fact content.",
                },
            },
            "required": ["query"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a stored fact by its numeric id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "integer",
                    "description": "The id of the fact to delete.",
                },
            },
            "required": ["fact_id"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]
