"""Shared agent-<id> derivation for auto-capture hooks.

MUST stay byte-identical to pretooluse_bash._agent_identity_key so the live
workboard and finished git history use ONE identifier for the same agent.
Pinned by tests/test_agent_ident.py::test_matches_commit_author_hook.
"""
import hashlib


def short_hash(value, length):
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def agent_key(data):
    """Build `agent-<6hash>` from session_id, plus `.sub<4hash>` when this call
    carries an agent_id (present only inside a sub-agent's own tool calls).
    Returns None if session_id is missing/empty."""
    session_id = data.get("session_id")
    if not session_id:
        return None
    key = f"agent-{short_hash(session_id, 6)}"
    agent_id = data.get("agent_id")
    if agent_id:
        key += f".sub{short_hash(agent_id, 4)}"
    return key
