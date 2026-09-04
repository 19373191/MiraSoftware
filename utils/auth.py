"""
M.I.R.A. Authentication and Role-Based Access Control (RBAC) Security Utility.
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Any, Callable, Dict, List, Optional, Union
from functools import wraps

try:
    from fastapi import Depends, HTTPException, Request, status
    from fastapi.security import OAuth2PasswordBearer
except ImportError:
    class Depends:
        def __init__(self, *args, **kwargs): pass
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str, headers: Optional[dict] = None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
    class Request: pass
    class OAuth2PasswordBearer:
        def __init__(self, *args, **kwargs): pass
    class status:
        HTTP_401_UNAUTHORIZED = 401
        HTTP_403_FORBIDDEN = 403

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any
from models.db import User, get_db
from utils.logger import logger

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "mira_enterprise_secret_key_2026_change_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12 hours

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)


# Password Security Utilities using PBKDF2-HMAC-SHA256 (standard & portable)
def hash_password(password: str) -> str:
    """Hashes a plain text password using PBKDF2-HMAC-SHA256."""
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2:sha256:{base64.b64encode(salt).decode('utf-8')}:{base64.b64encode(pwd_hash).decode('utf-8')}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain text password against stored PBKDF2 hash."""
    try:
        parts = hashed_password.split(":")
        if len(parts) != 4 or parts[0] != "pbkdf2" or parts[1] != "sha256":
            return False
        salt = base64.b64decode(parts[2].encode("utf-8"))
        stored_hash = base64.b64decode(parts[3].encode("utf-8"))
        new_hash = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, 100000)
        return hmac.compare_digest(stored_hash, new_hash)
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False


# JWT Token Utilities
def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _urlsafe_b64decode(data_str: str) -> bytes:
    padding = "=" * (4 - (len(data_str) % 4))
    return base64.urlsafe_b64decode(data_str + padding)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Generates a signed JWT access token containing user claims."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": int(expire.timestamp())})

    header = {"alg": ALGORITHM, "typ": "JWT"}
    header_json = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload_json = json.dumps(to_encode, separators=(",", ":")).encode("utf-8")

    encoded_header = _urlsafe_b64encode(header_json)
    encoded_payload = _urlsafe_b64encode(payload_json)

    signature_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), signature_input, hashlib.sha256).digest()
    encoded_signature = _urlsafe_b64encode(signature)

    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def decode_access_token(token: str) -> Dict[str, Any]:
    """Validates and decodes a JWT access token."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT token structure.")

    encoded_header, encoded_payload, encoded_signature = parts
    signature_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), signature_input, hashlib.sha256).digest()
    actual_sig = _urlsafe_b64decode(encoded_signature)

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid JWT token signature.")

    payload = json.loads(_urlsafe_b64decode(encoded_payload).decode("utf-8"))

    exp = payload.get("exp")
    if exp and datetime.now(timezone.utc).timestamp() > exp:
        raise ValueError("JWT token has expired.")

    return payload


# Authentication Dependencies
def get_current_user_from_token(token: str, db: Session) -> Optional[User]:
    """Helper retrieving User model from JWT token string."""
    try:
        payload = decode_access_token(token)
        user_id = payload.get("user_id") or payload.get("sub")
        if not user_id:
            return None
        user = db.query(User).filter((User.id == user_id) | (User.email == str(user_id))).first()
        if user and user.is_active:
            return user
    except Exception as e:
        logger.debug("Failed to resolve user from token: %s", e)
    return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency resolving the current authenticated user from cookie or Authorization header.
    """
    token = getattr(request, "cookies", {}).get("mira_access_token")
    if not token and hasattr(request, "headers"):
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_current_user_from_token(token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token or user account deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_optional_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """FastAPI dependency returning User if authenticated, or None if unauthenticated."""
    token = getattr(request, "cookies", {}).get("mira_access_token")
    if not token and hasattr(request, "headers"):
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if token:
        return get_current_user_from_token(token, db)
    return None


# RBAC Authorization Dependencies & Decorators
def require_role(allowed_roles: Union[str, List[str]]):
    """
    FastAPI Dependency factory enforcing Role-Based Access Control (RBAC).
    """
    roles = [allowed_roles] if isinstance(allowed_roles, str) else allowed_roles

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            logger.warning(
                "Access denied for user %s (role: %s). Required roles: %s",
                current_user.email,
                current_user.role,
                roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to access this resource.",
            )
        return current_user

    return role_checker


# Pre-constructed dependencies for common role checks
require_super_admin = require_role(["super_admin"])
require_admin_or_higher = require_role(["super_admin", "admin"])
require_active_user = require_role(["super_admin", "admin", "user"])
