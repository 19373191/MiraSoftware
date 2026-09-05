"""
M.I.R.A. Platform Connection Management Router.

Provides web and API endpoints to manage integrations with Monday.com and Xero OAuth 2.0.
"""

import time
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

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any
from config import settings
from connectors.monday_connector import MondayConnector
from models.db import Credentials, User, get_db
from utils.auth import get_current_user, get_optional_current_user, require_admin_or_higher
from utils.logger import logger
from utils.oauth_handler import OAuthHandler

router = APIRouter(tags=["Platform Connectors"])
templates = Jinja2Templates(directory="templates")


def get_user_credentials(db: Session, user_id: int, platform_name: str) -> Optional[Credentials]:
    """Helper to retrieve credentials for a user and platform."""
    if not db:
        return None
    return (
        db.query(Credentials)
        .filter(Credentials.user_id == user_id, Credentials.platform_name == platform_name)
        .first()
    )


@router.get("/connectors", response_class=HTMLResponse)
async def connectors_page(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Renders the platform connection management web page.
    Displays cards and connection status badges ('Connected', 'Disconnected', 'Token Expired').
    """
    user_id = getattr(current_user, "id", 1)
    monday_cred = get_user_credentials(db, user_id, "monday")
    xero_cred = get_user_credentials(db, user_id, "xero")

    # Evaluate Monday status
    if monday_cred and monday_cred.api_key and monday_cred.board_id:
        monday_status = "Connected"
    else:
        monday_status = "Disconnected"

    # Evaluate Xero status
    if xero_cred and xero_cred.access_token:
        if xero_cred.token_expiry and time.time() > xero_cred.token_expiry:
            if xero_cred.refresh_token:
                xero_status = "Connected"
            else:
                xero_status = "Token Expired"
        else:
            xero_status = "Connected"
    else:
        xero_status = "Disconnected"

    return templates.TemplateResponse(
        request,
        "connectors.html",
        {
            "current_user": current_user,
            "monday_cred": monday_cred,
            "xero_cred": xero_cred,
            "monday_status": monday_status,
            "xero_status": xero_status,
            "xero_client_id": settings.xero_client_id,
            "xero_client_secret": settings.xero_client_secret,
            "error": error,
            "success": success,
        },
    )


@router.post("/api/connectors/monday/save")
async def save_monday_credentials(
    request: Request,
    api_key: str = Form(...),
    board_id: Optional[str] = Form(None),
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """Saves or updates Monday.com API Key credentials in database."""
    clean_key = api_key.strip()
    clean_board = board_id.strip() if board_id else None
    user_id = getattr(current_user, "id", 1)

    cred = get_user_credentials(db, user_id, "monday")
    if not cred:
        cred = Credentials(
            user_id=user_id,
            platform_name="monday",
            api_key=clean_key,
            board_id=clean_board,
        )
        if db: db.add(cred)
    else:
        cred.api_key = clean_key
        if clean_board is not None:
            cred.board_id = clean_board

    if db: db.commit()
    logger.info("Saved Monday credentials for user %s", getattr(current_user, "email", "user"))
    return RedirectResponse(
        url="/connectors?success=Monday.com+credentials+saved+successfully.",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@router.post("/api/connectors/xero/save")
async def save_xero_app_credentials(
    request: Request,
    client_id: str = Form(...),
    client_secret: str = Form(...),
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """Saves Xero client ID and secret to the .env file and updates settings."""
    import os
    clean_id = client_id.strip()
    clean_secret = client_secret.strip()

    # Clear old token if client ID or secret has changed
    user_id = getattr(current_user, "id", 1)
    if settings.xero_client_id != clean_id or settings.xero_client_secret != clean_secret:
        cred = get_user_credentials(db, user_id, "xero")
        if cred and db:
            db.delete(cred)
            db.commit()
            logger.info("Cleared old Xero tokens because client credentials were updated.")
            
            # Also clear from mock_db.json
            try:
                import json
                mock_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_db.json")
                if os.path.exists(mock_db_path):
                    with open(mock_db_path, "r") as f:
                        mock_data = json.load(f)
                    if "credentials" in mock_data:
                        mock_data["credentials"] = [c for c in mock_data["credentials"] if not (c.get("user_id") == user_id and c.get("platform_name") == "xero")]
                        with open(mock_db_path, "w") as f:
                            json.dump(mock_data, f, indent=4)
            except Exception as mock_err:
                logger.warning("Could not clear old token in mock_db.json: %s", mock_err)

    # Read .env file lines
    env_path = ".env"
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()
    else:
        # Fallback to .env.example if it exists
        if os.path.exists(".env.example"):
            with open(".env.example", "r", encoding="utf-8") as f:
                env_lines = f.readlines()

    # Update or append Client ID and Client Secret
    id_found = False
    secret_found = False
    new_lines = []
    for line in env_lines:
        if line.strip().startswith("XERO_CLIENT_ID="):
            new_lines.append(f"XERO_CLIENT_ID={clean_id}\n")
            id_found = True
        elif line.strip().startswith("XERO_CLIENT_SECRET="):
            new_lines.append(f"XERO_CLIENT_SECRET={clean_secret}\n")
            secret_found = True
        else:
            new_lines.append(line)

    if not id_found:
        new_lines.append(f"XERO_CLIENT_ID={clean_id}\n")
    if not secret_found:
        new_lines.append(f"XERO_CLIENT_SECRET={clean_secret}\n")

    # Write back to .env
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Dynamic reload in running settings
    settings.xero_client_id = clean_id
    settings.xero_client_secret = clean_secret

    logger.info("Saved Xero app credentials to .env for user %s", getattr(current_user, "email", "user"))
    return RedirectResponse(
        url="/connectors?success=Xero+Developer+credentials+saved+successfully.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/api/connectors/monday/test")
async def test_monday_connection(
    request: Request,
    api_key: Optional[str] = Form(None),
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Tests Monday.com GraphQL API connection using me or boards query.
    """
    user_id = getattr(current_user, "id", 1)
    cred = get_user_credentials(db, user_id, "monday")
    target_api_key = api_key or (cred.api_key if cred else None) or settings.monday_api_key

    if not target_api_key:
        return {"status": "error", "message": "Monday API Key is missing. Please save your API Key first."}

    connector = MondayConnector(api_key=target_api_key)
    try:
        query = "query { me { id name email } }"
        res = connector.execute_query(query)
        if "data" in res and res["data"].get("me"):
            me_info = res["data"]["me"]
            logger.info("Monday connection test succeeded for user: %s", me_info.get("name"))
            return {
                "status": "success",
                "message": f"Successfully connected to Monday.com as '{me_info.get('name')}' ({me_info.get('email')})",
                "details": me_info,
            }
        elif "errors" in res:
            return {"status": "error", "message": f"Monday API returned errors: {res['errors']}"}
        else:
            return {"status": "success", "message": "Connection verified with Monday.com GraphQL API."}
    except Exception as exc:
        logger.error("Monday connection test failed: %s", exc)
        return {"status": "error", "message": f"Connection test failed: {str(exc)}"}


def get_xero_redirect_uri(request: Request) -> str:
    """
    Dynamically computes the Xero OAuth redirect URI.
    Supports local development (localhost:8000), production on Render/cloud (HTTPS),
    and explicit XERO_REDIRECT_URI environment configurations.
    """
    # 1. If explicit production redirect URI is set in settings and not pointing to localhost
    if settings.xero_redirect_uri and "localhost" not in settings.xero_redirect_uri and "127.0.0.1" not in settings.xero_redirect_uri:
        uri = settings.xero_redirect_uri.strip()
        if not (uri.endswith("/oauth/xero/callback") or uri.endswith("/callback")):
            uri = uri.rstrip("/") + "/oauth/xero/callback"
        return uri

    # 2. Derive dynamically from request headers (supporting reverse proxies like Render)
    proto = request.headers.get("x-forwarded-proto") or (request.url.scheme if hasattr(request, "url") else "http")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"

    # Enforce https if on cloud / external domain (like onrender.com)
    if "localhost" not in host and "127.0.0.1" not in host:
        proto = "https"

    return f"{proto}://{host}/oauth/xero/callback"


@router.get("/oauth/xero/connect")
async def xero_oauth_connect(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Redirects user to Xero OAuth 2.0 authorization endpoint."""
    user_id = getattr(current_user, "id", 1)
    redirect_uri = get_xero_redirect_uri(request)

    oauth_handler = OAuthHandler(redirect_uri=redirect_uri)
    auth_url = oauth_handler.get_authorization_url(state=f"user_{user_id}")
    logger.info("Redirecting user %s to Xero consent URL (redirect_uri: %s)...", getattr(current_user, "email", "user"), redirect_uri)
    return RedirectResponse(url=auth_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/oauth/xero/callback")
@router.get("/callback")
async def xero_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    OAuth 2.0 callback endpoint handling Xero authorization code exchange.
    Stores tokens in database and redirects back to /connectors.
    """
    if error:
        logger.error("Xero OAuth callback returned error: %s", error)
        return RedirectResponse(
            url=f"/connectors?error=Xero+Authorization+Error:+{error}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not code:
        return RedirectResponse(
            url="/connectors?error=Missing+authorization+code+from+Xero.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    current_user = get_optional_current_user(request, db)
    if not current_user:
        if state and db:
            try:
                # state starts with user_<id>
                parts = state.split("_")
                if len(parts) >= 2 and parts[0] == "user":
                    uid = int(parts[1])
                    current_user = db.query(User).filter(User.id == uid).first()
            except ValueError:
                pass

    if not current_user and db:
        return RedirectResponse(
            url="/login?error=Session+expired+during+OAuth+flow.+Please+login.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if current_user and current_user.role not in ("super_admin", "admin"):
        logger.warning("User '%s' with role '%s' attempted to complete Xero OAuth authorization.", current_user.email, current_user.role)
        return RedirectResponse(
            url="/?error=Insufficient+permissions+to+configure+connectors.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        redirect_uri = get_xero_redirect_uri(request)
        oauth_handler = OAuthHandler(redirect_uri=redirect_uri)
        token_data = oauth_handler.exchange_code_for_token(code)

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 1800)
        token_expiry = time.time() + expires_in
        user_id = getattr(current_user, "id", 1)

        cred = get_user_credentials(db, user_id, "xero")
        if not cred:
            cred = Credentials(
                user_id=user_id,
                platform_name="xero",
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
            )
            if db: db.add(cred)
        else:
            cred.access_token = access_token
            cred.refresh_token = refresh_token
            cred.token_expiry = token_expiry

        if db: db.commit()

        # Also sync to mock_db.json for fallback Flask environment
        try:
            import json, os
            mock_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_db.json")
            mock_data = {}
            if os.path.exists(mock_db_path):
                try:
                    with open(mock_db_path, "r") as f:
                        mock_data = json.load(f)
                except Exception:
                    pass
            if "credentials" not in mock_data:
                mock_data["credentials"] = []
            
            # Remove existing xero credential for this user
            mock_data["credentials"] = [c for c in mock_data["credentials"] if not (c.get("user_id") == user_id and c.get("platform_name") == "xero")]
            
            mock_data["credentials"].append({
                "id": len(mock_data["credentials"]) + 1,
                "user_id": user_id,
                "platform_name": "xero",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expiry": token_expiry
            })
            with open(mock_db_path, "w") as f:
                json.dump(mock_data, f, indent=4)
        except Exception as e:
            logger.warning("Could not sync Xero credentials to mock_db.json: %s", e)

        logger.info("Successfully connected Xero OAuth for user %s", getattr(current_hash_user := current_user, "email", "user"))
        return RedirectResponse(
            url="/connectors?success=Xero+Developer+Sandbox+connected+successfully!",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as exc:
        logger.error("Failed to exchange Xero auth code: %s", exc)
        return RedirectResponse(
            url=f"/connectors?error=Token+exchange+failed:+{str(exc)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/api/connectors/xero/disconnect")
async def disconnect_xero(
    request: Request,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """Invalidates and removes stored Xero tokens for the current user."""
    user_id = getattr(current_user, "id", 1)
    cred = get_user_credentials(db, user_id, "xero")
    if cred and db:
        db.delete(cred)
        db.commit()
        logger.info("Disconnected Xero credentials for user %s", getattr(current_user, "email", "user"))

    return RedirectResponse(
        url="/connectors?success=Disconnected+from+Xero.",
        status_code=status.HTTP_303_SEE_OTHER,
    )
