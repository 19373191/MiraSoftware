"""
M.I.R.A. OAuth2 Handler Utility.

Manages OAuth2 flows, authorization headers, JWT encoding/decoding, and token lifecycle for third-party integrations like Xero.
"""

from typing import Any, Dict, Optional
import time
import requests

try:
    import jwt
except ImportError:
    jwt = None

from config import settings
from utils.logger import logger

DEFAULT_XERO_REDIRECT_URI = "https://localhost"
DEFAULT_XERO_SCOPES = "accounting.invoices accounting.contacts accounting.settings offline_access"


class OAuthHandler:
    """OAuth2 authentication and token manager for Xero integration."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        token_url: str = "https://identity.xero.com/connect/token",
        auth_url: str = "https://login.xero.com/identity/connect/authorize",
    ):
        self.client_id = client_id or settings.xero_client_id
        self.client_secret = client_secret or settings.xero_client_secret
        self.redirect_uri = redirect_uri or settings.xero_redirect_uri or DEFAULT_XERO_REDIRECT_URI
        self.token_url = token_url
        self.auth_url = auth_url
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at: float = 0.0

    def get_authorization_url(
        self,
        scope: Optional[str] = None,
        state: Optional[str] = "mira_auth_state",
    ) -> str:
        """
        Generates the Xero OAuth 2.0 authorization URL using redirect_uri (default: https://localhost).

        Args:
            scope: Space-separated OAuth scopes requested. Defaults to standard Xero scopes.
            state: Anti-forgery state parameter.

        Returns:
            str: Formatted authorization URL.
        """
        import urllib.parse
        scopes = scope or DEFAULT_XERO_SCOPES
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scopes,
            "state": state,
        }
        query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        url = f"{self.auth_url}?{query_string}"
        logger.info(
            "Generated Xero OAuth authorization URL (client_id: %s, redirect_uri: %s)",
            self.client_id,
            self.redirect_uri,
        )
        return url

    def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """
        Exchanges authorization code for access token and refresh token.

        Args:
            code: OAuth authorization code received from callback.

        Returns:
            Dict[str, Any]: Token response dictionary containing access_token and refresh_token.
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        logger.info("Exchanging authorization code for Xero access token...")
        response = requests.post(self.token_url, data=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 1800)
        self._expires_at = time.time() + expires_in

        logger.info("Successfully acquired Xero access token. Expires in %s seconds.", expires_in)
        return data

    def refresh_access_token(self) -> Dict[str, Any]:
        """
        Refreshes an expired access token using the stored refresh token.

        Returns:
            Dict[str, Any]: Refreshed token payload.
        """
        if not self._refresh_token:
            logger.error("Refresh token attempt failed: No refresh token stored.")
            raise ValueError("No refresh token available to perform refresh.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        logger.info("Refreshing Xero access token...")
        response = requests.post(self.token_url, data=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        self._access_token = data.get("access_token")
        if "refresh_token" in data:
            self._refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 1800)
        self._expires_at = time.time() + expires_in

        logger.info("Xero access token successfully refreshed.")
        return data

    def is_token_expired(self) -> bool:
        """Checks if current access token is expired or close to expiry (within 60s buffer)."""
        if not self._access_token or not self._expires_at:
            return True
        return time.time() >= (self._expires_at - 60)

    def ensure_valid_token(self) -> str:
        """
        Automatic token refresh helper function that ensures an active, non-expired access token.
        Refreshes token automatically if expired.

        Returns:
            str: Valid access token.
        """
        if self.is_token_expired():
            logger.info("Token expired or missing. Triggering automatic token refresh...")
            if self._refresh_token:
                self.refresh_access_token()
            else:
                logger.warning("Token expired and no refresh token available.")

        if not self._access_token:
            raise ValueError("Access token is missing or unauthorized.")

        return self._access_token

    def get_bearer_header(self) -> Dict[str, str]:
        """
        Returns Bearer Authorization header, automatically refreshing token if expired.

        Returns:
            Dict[str, str]: Authorization header dictionary.
        """
        token = self.ensure_valid_token()
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def decode_jwt(token: str, secret: str = "", verify: bool = False) -> Dict[str, Any]:
        """
        Decodes a JWT token using PyJWT.

        Args:
            token: Encoded JWT string.
            secret: Secret key for verification (if enabled).
            verify: Whether to verify signature.

        Returns:
            Dict[str, Any]: Decoded payload.
        """
        if jwt is None:
            raise ImportError("pyjwt package is required for JWT decoding.")
        options = {"verify_signature": verify}
        return jwt.decode(token, secret, algorithms=["HS256", "RS256"], options=options)
