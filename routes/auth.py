"""
M.I.R.A. Authentication & Super-Admin Seeding Router.
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.templating import Jinja2Templates
except ImportError:
    class APIRouter:
        def __init__(self, *args, **kwargs): pass
        def __getattr__(self, name): return lambda *a, **k: lambda f: f
    class Depends:
        def __init__(self, *args, **kwargs): pass
    def Form(*args, **kwargs): return None
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            self.status_code = status_code
            self.detail = detail
    class Request: pass
    class Response: pass
    class HTMLResponse: pass
    class RedirectResponse: pass
    class Jinja2Templates:
        def __init__(self, *args, **kwargs): pass
        def TemplateResponse(self, *args, **kwargs): return {}
    class status:
        HTTP_303_SEE_OTHER = 303
        HTTP_401_UNAUTHORIZED = 401
        HTTP_403_FORBIDDEN = 403

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any
from models.db import User, get_db
from utils.auth import (
    create_access_token,
    get_optional_current_user,
    hash_password,
    verify_password,
)
from utils.logger import logger

router = APIRouter(tags=["Authentication"])
templates = Jinja2Templates(directory="templates")


def seed_super_admin(db: Session) -> None:
    """
    Seeds initial super-admin accounts and restores all accounts from users_registry.json.
    Ensures accounts are never deleted or lost upon application restart or container cold-start.
    """
    try:
        # 1. Ensure core system accounts exist
        core_accounts = [
            ("admin@mira.com", "Admin123!", "super_admin", True),
            ("admin@mira.local", "AdminPass123!", "admin", True),
        ]
        for email_addr, pwd, role, is_active in core_accounts:
            existing = db.query(User).filter(User.email == email_addr).first() if db else None
            if not existing and db:
                admin_user = User(
                    email=email_addr,
                    hashed_password=hash_password(pwd),
                    role=role,
                    is_active=is_active,
                )
                db.add(admin_user)
                db.commit()
                logger.info("Successfully seeded core system account: %s", email_addr)

        # 2. Restore all registered users from users_registry.json
        registry_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "users_registry.json")
        if os.path.exists(registry_path) and db:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            for item in registry:
                u_email = item.get("email")
                if not u_email:
                    continue
                user_record = db.query(User).filter(User.email == u_email).first()
                if not user_record:
                    reg_user = User(
                        email=u_email,
                        hashed_password=item.get("hashed_password") or hash_password("Password123!"),
                        role=item.get("role", "user"),
                        is_active=item.get("is_active", True),
                    )
                    db.add(reg_user)
                    db.commit()
                    logger.info("Restored registered user from registry: %s (%s)", u_email, item.get("role"))
    except Exception as e:
        logger.warning("Could not seed users: %s", e)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    """Renders the login page template."""
    current_user = get_optional_current_user(request, db)
    if current_user:
        target_url = "/admin/users" if current_user.role in ("super_admin", "admin") else "/sync"
        return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Handles login form submissions, validates credentials, and sets HTTP-only JWT cookie.
    """
    clean_email = email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()

    if not user or not verify_password(password, user.hashed_password):
        logger.warning("Failed login attempt for email: %s", clean_email)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        logger.warning("Login attempt for deactivated user account: %s", clean_email)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Account is deactivated. Please contact an administrator."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Issue JWT token
    access_token = create_access_token(
        data={"sub": user.email, "user_id": user.id, "role": user.role}
    )

    target_url = "/admin/users" if user.role in ("super_admin", "admin") else "/sync"
    response = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)

    # Set HTTP-Only Cookie with 30-day persistent expiration across tab closures
    if hasattr(response, "set_cookie"):
        expire_date = datetime.now(timezone.utc) + timedelta(days=30)
        response.set_cookie(
            key="mira_access_token",
            value=access_token,
            httponly=True,
            max_age=60 * 60 * 24 * 30,  # 30 days
            expires=int(expire_date.timestamp()),
            path="/",
            samesite="lax",
        )
    logger.info("User '%s' logged in successfully. Redirecting to %s", user.email, target_url)
    return response


@router.get("/logout")
async def logout():
    """Clears authentication cookie and redirects to login page."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if hasattr(response, "delete_cookie"):
        response.delete_cookie("mira_access_token")
    logger.info("User logged out successfully.")
    return response
