import importlib.util, hashlib
from pathlib import Path
import agent_ident


def test_agent_key_matches_algorithm():
    assert agent_ident.agent_key({"session_id": "sess"}) == "agent-" + hashlib.sha1(b"sess").hexdigest()[:6]
    key = agent_ident.agent_key({"session_id": "sess", "agent_id": "subby"})
    assert key == "agent-" + hashlib.sha1(b"sess").hexdigest()[:6] + ".sub" + hashlib.sha1(b"subby").hexdigest()[:4]
    assert agent_ident.agent_key({}) is None            # no session_id -> None
    assert agent_ident.agent_key({"session_id": ""}) is None


def test_matches_commit_author_hook():
    # Load the LIVE commit-identity hook and assert identical output, so the
    # board's agent id can never drift from what lands in git history.
    p = Path.home() / ".claude" / "hooks" / "pretooluse_bash.py"
    spec = importlib.util.spec_from_file_location("pb_live", p)
    pb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb)
    for data in ({"session_id": "abc"}, {"session_id": "abc", "agent_id": "def"},
                 {"session_id": "9f8e7d", "agent_id": "z"}):
        assert agent_ident.agent_key(data) == pb._agent_identity_key(data)
