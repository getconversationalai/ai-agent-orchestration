"""agent_state.identity -- stable repo identity from git remotes.

Resolves a repo's identity (host/owner/name and a normalised remote key)
purely from local `git remote` output -- no network calls. `github_repo_id`
is always None here; it is populated later (Task 8/10) from a local cache
written by the drainer/sign-in flow.

Never raises: a malformed remote, a repo with no "origin" remote, a path
that isn't a git repo at all, or a machine with no `git` on PATH all
produce the same all-None result rather than an exception.

Trap this module avoids
------------------------
An earlier draft of this resolver used a regex that matched *any* URL
scheme, including `file://`. That let `file:///tmp/x` slip through and
normalise to `"file/tmp/x"` -- a plausible-looking but wrong key -- and it
failed its own test. This version allowlists the scheme (ssh/http/https)
*before* any regex runs, so file://, git://, and bare local paths are
rejected outright rather than relying on the regex to reject them.
"""

import re
import shutil
import subprocess

# Scheme allowlist is checked before any regex match -- see module
# docstring. ssh and http(s) are the only remote forms this resolver
# normalises; everything else returns None.
_ALLOWED_SCHEMES = ("http://", "https://", "ssh://")

_HTTP_RE = re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")
_SSH_RE = re.compile(r"^(?:ssh://)?(?:[^@/]+@)?([^:/]+):?/?(.+?)(?:\.git)?/?$")

_EMPTY_IDENTITY = {
    "github_repo_id": None,
    "remote_key": None,
    "host": None,
    "owner": None,
    "name": None,
}


def normalise_remote(raw):
    """Normalise a git remote URL to a stable ``host/owner/name`` key.

    ssh and https forms of the same repo normalise to the same key.
    Credentials embedded in the URL (``https://user:token@host/...``) are
    stripped by the regex's credential group and never appear in the
    returned key. Any scheme other than ssh/http(s) -- ``file://``,
    ``git://``, bare local paths, or unparseable garbage -- returns None.

    Never raises.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    if "://" in raw and not raw.startswith(_ALLOWED_SCHEMES):
        return None  # file://, git://, etc. -- reject before regex matching

    match = _HTTP_RE.match(raw)
    if match is None and ("://" not in raw or raw.startswith("ssh://")):
        match = _SSH_RE.match(raw)
    if match is None:
        return None

    host = match.group(1).lower()
    path = match.group(2).lower().strip("/")
    if not host or "/" not in path:
        return None
    return f"{host}/{path}"


def _get_remote_url(repo_path, remote="origin"):
    """Return the raw URL configured for ``remote``, or None.

    Returns None (never raises) when git isn't on PATH, ``repo_path`` isn't
    a git repo, the remote doesn't exist, or anything else goes wrong.
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", remote],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def resolve_identity(repo_path):
    """Resolve a repo's identity from its ``origin`` remote.

    Returns a dict with keys ``github_repo_id``, ``remote_key``, ``host``,
    ``owner``, ``name``. ``github_repo_id`` is always None here (this
    function makes no network calls); a later task populates it from a
    local cache. Any failure -- missing git, absent repo, no remote,
    unparseable remote URL, or a malformed ``repo_path`` -- yields
    all-None fields rather than raising.

    Never raises.
    """
    try:
        raw = _get_remote_url(repo_path)
        remote_key = normalise_remote(raw)
        if remote_key is None:
            return dict(_EMPTY_IDENTITY)

        parts = remote_key.split("/")
        host = parts[0]
        name = parts[-1]
        owner = "/".join(parts[1:-1]) or None

        return {
            "github_repo_id": None,
            "remote_key": remote_key,
            "host": host,
            "owner": owner,
            "name": name,
        }
    except Exception:
        return dict(_EMPTY_IDENTITY)
