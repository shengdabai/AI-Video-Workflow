from __future__ import annotations

import hmac
import os
from uuid import uuid4

from fastapi import HTTPException, Request
from loguru import logger

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def require_write_auth(request: Request) -> None:
    """Protect mutating API endpoints with PIXELLE_API_TOKEN or loopback restriction."""
    expected = os.environ.get("PIXELLE_API_TOKEN", "").strip()
    if expected:
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not hmac.compare_digest(token.strip(), expected):
            raise HTTPException(status_code=403, detail="Invalid API token")
        return

    if os.environ.get("PIXELLE_REQUIRE_AUTH", "").strip() == "1":
        raise HTTPException(
            status_code=401,
            detail="API authentication required; configure PIXELLE_API_TOKEN",
        )
    client_host = request.client.host if request.client is not None else None
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=401,
            detail="API authentication required; configure PIXELLE_API_TOKEN",
        )


def internal_error(context: str, exc: Exception) -> HTTPException:
    """Log the underlying error with a request ID and return a generic 500."""
    request_id = uuid4().hex[:12]
    logger.exception(f"[{request_id}] {context}: {exc}")
    return HTTPException(
        status_code=500,
        detail=f"Internal server error (request_id={request_id})",
    )
