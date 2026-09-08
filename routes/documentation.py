"""
M.I.R.A. System Documentation & Interactive User Guide Router.

Provides comprehensive platform documentation, onboarding guides, role permissions matrix,
connector setup walkthroughs, schema mapping instructions, and synchronization operations.
Accessible to all authenticated users across all roles (super_admin, admin, user).
"""

from typing import Any, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.templating import Jinja2Templates
except ImportError:
    class APIRouter:
        def __init__(self, *args, **kwargs): pass
        def __getattr__(self, name): return lambda *a, **k: lambda f: f
    class Depends:
        def __init__(self, *args, **kwargs): pass
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

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any

from models.db import User, get_db
from utils.auth import get_optional_current_user, get_current_user
from utils.logger import logger

router = APIRouter(tags=["Documentation"])
templates = Jinja2Templates(directory="templates")


@router.get("/documentation", response_class=HTMLResponse)
async def documentation_page(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Renders the comprehensive platform documentation and onboarding guide.
    Accessible to all platform roles (super_admin, admin, user, operator, viewer).
    """
    current_user = get_optional_current_user(request, db)
    if not current_user:
        return RedirectResponse(
            url="/login?error=Please+log+in+to+access+system+documentation.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return templates.TemplateResponse(
        request,
        "documentation.html",
        {
            "current_user": current_user,
            "error": error,
            "success": success,
        },
    )


@router.get("/docs-guide", response_class=HTMLResponse)
async def documentation_guide_alias(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Alias for /documentation."""
    return await documentation_page(request, error=error, success=success, db=db)
