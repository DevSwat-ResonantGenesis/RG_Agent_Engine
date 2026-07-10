"""Scope enforcement for RG_Auth's WorkspaceAccessToken (RGW- tokens).

Every /agents/* and Builder write endpoint in this service trusts an
x-user-id header with no further check - fine for a real user session
(Gateway already validated the cookie/JWT), but this service is now also
reachable via a scoped RGW- token (minted for one workspace, injected into
that workspace's sandboxed terminal). Gateway's auth_middleware.py injects
x-token-scopes only when the caller used an RGW- token; a normal session
never sets that header, so require_scope is a no-op for the dashboard UI
and only actually gates requests coming from a workspace token.
"""
from fastapi import HTTPException, Request


def caller_scopes(request: Request) -> list[str] | None:
    """None means "not a scoped token, full access" (normal user session).
    A list (even empty) means the caller used an RGW- token with exactly
    these scopes."""
    raw = request.headers.get("x-token-scopes")
    if raw is None:
        return None
    return [s for s in raw.split(",") if s]


def require_scope(request: Request, scope: str) -> None:
    """Raise 403 if this request came in on a scoped token that doesn't
    grant `scope`. A wildcard like "agents:*" satisfies any "agents:..."
    check. Does nothing for normal user sessions (caller_scopes is None).
    """
    scopes = caller_scopes(request)
    if scopes is None:
        return

    category = scope.split(":", 1)[0]
    if scope in scopes or f"{category}:*" in scopes:
        return

    raise HTTPException(status_code=403, detail=f"Workspace token missing required scope: {scope}")
