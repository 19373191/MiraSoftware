"""
M.I.R.A. Authentication & Super-Admin Seeding Router.
"""

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
    Seeds initial super-admin account (admin@mira.com / Admin123!) if not present.
    """
    admin_emails = ["admin@mira.com", "admin@mira.local"]
    try:
        for email_addr in admin_emails:
            existing = db.query(User).filter(User.email == email_addr).first() if db else None
            if not existing and db:
                pwd = "Admin123!" if email_addr == "admin@mira.com" else "AdminPass123!"
                admin_user = User(
                    email=email_addr,
                    hashed_password=hash_password(pwd),
                    role="super_admin" if email_addr == "admin@mira.com" else "admin",
                    is_active=True,
                )
                db.add(admin_user)
                db.commit()
                logger.info("Successfully seeded super-admin account: %s", email_addr)
    except Exception as e:
        logger.warning("Could not seed super-admin: %s", e)


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

    # Set HTTP-Only Cookie
    if hasattr(response, "set_cookie"):
        response.set_cookie(
            key="mira_access_token",
            value=access_token,
            httponly=True,
            max_age=60 * 60 * 12,  # 12 hours
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
