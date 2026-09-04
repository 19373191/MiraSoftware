"""
M.I.R.A. Admin User Management Router.

Enforces Role-Based Access Control (RBAC) allowing only 'admin' role users to view,
create, and toggle status for platform accounts.
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
        HTTP_404_NOT_FOUND = 404

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any
from models.db import User, get_db
from utils.auth import get_current_user, hash_password, require_role, require_admin_or_higher
from utils.logger import logger

router = APIRouter(prefix="/admin", tags=["Admin User Management"])
templates = Jinja2Templates(directory="templates")


@router.get("/debug-role")
async def debug_role(current_user: User = Depends(get_current_user)):
    return {
        "email": current_user.email,
        "role": current_user.role,
        "is_active": current_user.is_active,
    }


@router.get("/users", response_class=HTMLResponse)
async def admin_list_users(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Displays the user management console displaying all registered platform users.
    Restricted strictly to users with admin or super_admin roles.
    """
    if current_user.role == "admin":
        users = db.query(User).filter(User.role == "user").order_by(User.id.asc()).all() if db else []
    else:
        users = db.query(User).order_by(User.id.asc()).all() if db else []
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "current_user": current_user,
            "users": users,
            "error": error,
            "success": success,
        },
    )


@router.post("/users/create")
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Endpoint allowing System Owners or Administrators to create new user accounts.
    Administrators are restricted to creating Operators ('user' role).
    """
    clean_email = email.strip().lower()
    role_val = role.strip().lower()

    if current_user.role == "admin":
        # Administrators can only create Operators
        role_val = "user"
    else:
        # System Owners can assign any role
        if role_val not in ("super_admin", "admin", "user"):
            role_val = "user"

    existing_user = db.query(User).filter(User.email == clean_email).first() if db else None
    if existing_user:
        logger.warning("Admin '%s' attempted to create duplicate user: %s", getattr(current_user, "email", "admin"), clean_email)
        return RedirectResponse(
            url=f"/admin/users?error=User+with+email+'{clean_email}'+already+exists.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if len(password) < 6:
        return RedirectResponse(
            url="/admin/users?error=Password+must+be+at+least+6+characters+long.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    new_user = User(
        email=clean_email,
        hashed_password=hash_password(password),
        role=role_val,
        is_active=True,
    )
    if db:
        db.add(new_user)
        db.commit()

    logger.info("Admin '%s' created new user '%s' with role '%s'", getattr(current_user, "email", "admin"), clean_email, role_val)
    return RedirectResponse(
        url=f"/admin/users?success=User+'{clean_email}'+created+successfully.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


require_admin_role = require_role(["admin"])


@router.post("/users/{user_id}/toggle")
@router.post("/users/{user_id}/toggle-active")
async def admin_toggle_user_active(
    user_id: int,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Activates or deactivates a user account. Prevents self-deactivation for System Owners,
    and restricts Administrators to only toggling Operators.
    """
    target_user = db.query(User).filter(User.id == user_id).first() if db else None
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found.")

    # 1. Administrators can only toggle Operators
    if current_user.role == "admin" and target_user.role != "user":
        logger.warning("Admin '%s' attempted unauthorized status toggle of user '%s' (role: %s)", current_user.email, target_user.email, target_user.role)
        return RedirectResponse(
            url="/admin/users?error=You+do+not+have+permission+to+modify+this+account.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # 2. Prevent self-deactivation of core System Owner accounts
    if target_user.email in ("admin@mira.com", "admin@mira.local") and getattr(current_user, "id", None) == target_user.id:
        logger.warning("Admin '%s' attempted self-deactivation.", getattr(current_user, "email", "admin"))
        return RedirectResponse(
            url="/admin/users?error=System+Owner+account+cannot+be+deactivated.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    target_user.is_active = not target_user.is_active
    if db:
        db.commit()

    new_state = "activated" if target_user.is_active else "deactivated"
    logger.info("Admin '%s' %s user account '%s'", getattr(current_user, "email", "admin"), new_state, target_user.email)

    return RedirectResponse(
        url=f"/admin/users?success=User+'{target_user.email}'+has+been+{new_state}.",
        status_code=status.HTTP_303_SEE_OTHER,
    )
