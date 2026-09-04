"""
M.I.R.A. Configuration Module.

Loads and validates environment variables using Pydantic Settings (or fallback to environment variables).
"""

import os
from typing import Optional

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        """Application settings backed by environment variables."""

        app_name: str = "M.I.R.A. Middleware"
        app_env: str = "development"
        debug: bool = False
        log_level: str = "INFO"
        host: str = "0.0.0.0"
        port: int = 8000

        # Monday.com Configuration
        monday_api_key: Optional[str] = None
        monday_board_id: Optional[str] = None

        # Xero API Configuration
        xero_client_id: Optional[str] = None
        xero_client_secret: Optional[str] = None
        xero_redirect_uri: Optional[str] = "http://localhost:8000/callback"
        xero_tenant_id: Optional[str] = None

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
        )

except ImportError:
    from pydantic import BaseModel

    class Settings(BaseModel):
        """Fallback application settings."""

        app_name: str = os.getenv("APP_NAME", "M.I.R.A. Middleware")
        app_env: str = os.getenv("APP_ENV", "development")
        debug: bool = os.getenv("DEBUG", "False").lower() in ("true", "1")
        log_level: str = os.getenv("LOG_LEVEL", "INFO")
        host: str = os.getenv("HOST", "0.0.0.0")
        port: int = int(os.getenv("PORT", "8000"))

        monday_api_key: Optional[str] = os.getenv("MONDAY_API_KEY")
        monday_board_id: Optional[str] = os.getenv("MONDAY_BOARD_ID")

        xero_client_id: Optional[str] = os.getenv("XERO_CLIENT_ID")
        xero_client_secret: Optional[str] = os.getenv("XERO_CLIENT_SECRET")
        xero_redirect_uri: Optional[str] = os.getenv("XERO_REDIRECT_URI", "http://localhost:8000/callback")
        xero_tenant_id: Optional[str] = os.getenv("XERO_TENANT_ID")


settings = Settings()
