"""Admin API key authentication dependency.

Provides a FastAPI dependency that verifies the X-Admin-Key header against
the ADMIN_API_KEY setting. If the key is not configured (empty string), ALL
requests are rejected with 403 -- this is fail-closed by design.
"""

from __future__ import annotations

import hmac

from fastapi import Header

from app.settings import settings


class AdminAuthError(Exception):
    """Raised when admin authentication fails.

    Handled by the exception handler registered in main.py to produce
    a consistent JSON error response matching the project convention.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Verify the admin API key header.

    If ADMIN_API_KEY is not configured (empty string), ALL requests are
    rejected with 403. This is fail-closed by design.

    Uses hmac.compare_digest() for timing-safe string comparison to
    prevent timing attacks on the admin key.

    Raises:
        AdminAuthError: 403 if the key is missing, wrong, or not configured.
    """
    if not settings.admin_api_key:
        raise AdminAuthError(
            "AUTH_NOT_CONFIGURED",
            "Admin API key not configured. Set ADMIN_API_KEY in environment.",
        )

    if not hmac.compare_digest(x_admin_key or "", settings.admin_api_key):
        raise AdminAuthError(
            "FORBIDDEN",
            "Invalid or missing admin API key.",
        )
