"""OAuth2 password bearer scheme (stub).

Wire this into protected routes when auth is implemented.
"""

from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)
