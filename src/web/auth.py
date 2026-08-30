"""
Authentication helper for Cubey Web Interface.
"""

import secrets
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config import config

security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth username and password against configured environment."""
    expected_user = config.web_username or "admin"
    expected_pass = config.web_password or "cubey"

    correct_username = secrets.compare_digest(credentials.username, expected_user)
    correct_password = secrets.compare_digest(credentials.password, expected_pass)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def check_auth_token(token: Optional[str] = None) -> bool:
    """Validate query token or password for WebSocket connections."""
    expected_pass = config.web_password or "cubey"
    if token and secrets.compare_digest(token, expected_pass):
        return True
    return False
