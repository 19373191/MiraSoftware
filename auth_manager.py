"""
Xero API OAuth 2.0 Authentication Manager.

This module provides the XeroAuthorisationManager class which handles generating
the authorisation URL, exchanging authorisation codes for tokens, caching tokens
securely in a local JSON storage file, checking for token expiry, and automatically
refreshing the token when it is nearing expiry.

This script adheres strictly to UK English spelling for internal variables,
JSON keys, logging, and docstrings.
"""

import base64
import json
import logging
import os
import time
import urllib.parse
from typing import Dict, Optional

import requests

# Set up logging using UK English terminology
logger = logging.getLogger("XeroAuthorisationManager")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# Xero OAuth 2.0 API Endpoints
XERO_AUTHORISATION_ENDPOINT = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_ENDPOINT = "https://identity.xero.com/connect/token"


class XeroAuthorisationError(Exception):
    """Custom exception class for Xero OAuth 2.0 authorisation errors."""
    pass


class XeroAuthorisationManager:
    """
    Manages OAuth 2.0 authorisation for the Xero API.
    Handles token retrieval, storage persistence, and automated refreshing.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = "https://localhost",
        token_storage_path: str = "token_storage.json",
        expiry_threshold_seconds: int = 300  # Default to 5 minutes before expiry
    ):
        """
        Initialise the Xero authorisation manager.

        Args:
            client_id: The client credentials application identifier.
            client_secret: The client credentials application shared secret.
            redirect_uri: The URI to which Xero redirects after authorisation.
            token_storage_path: The local file path to securely store tokens.
            expiry_threshold_seconds: Remaining token lifespan in seconds below which
                                      a refresh is triggered.
        """
        if not client_id or not client_secret:
            raise ValueError("Both client_id and client_secret must be provided.")

        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_storage_path = os.path.abspath(token_storage_path)
        self.expiry_threshold_seconds = expiry_threshold_seconds

        logger.info(
            "Initialised XeroAuthorisationManager. Token storage configured at: %s",
            self.token_storage_path
        )

    def generate_authorisation_url(
        self,
        scope: str = "openid profile email accounting.transactions accounting.settings accounting.invoices accounting.contacts offline_access",
        state: Optional[str] = None
    ) -> str:
        """
        Generate the initial authorisation URL to redirect the user to Xero.

        Note: 'offline_access' scope is required to obtain a refresh token.

        Args:
            scope: Space-separated list of scopes to request.
            state: Optional random string to prevent CSRF attacks.

        Returns:
            The URL to redirect the user to.
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
        }
        if state:
            params["state"] = state

        query_string = urllib.parse.urlencode(params)
        authorisation_url = f"{XERO_AUTHORISATION_ENDPOINT}?{query_string}"
        
        logger.info("Authorisation URL generated successfully.")
        return authorisation_url

    def exchange_authorisation_code(self, authorisation_code: str) -> Dict[str, any]:
        """
        Exchange the authorisation code for an access token and a refresh token.

        Args:
            authorisation_code: The code returned from Xero after user consent.

        Returns:
            The dictionary containing token data stored in the local file.
        """
        if not authorisation_code:
            raise XeroAuthorisationError("Authorisation code cannot be empty.")

        logger.info("Exchanging authorisation code for tokens...")

        payload = {
            "grant_type": "authorization_code",  # Defined by OAuth 2.0 standard API, must keep 'z'
            "code": authorisation_code,
            "redirect_uri": self.redirect_uri,
        }

        token_data = self._make_token_request(payload)
        saved_data = self._process_and_save_token_response(token_data)
        
        logger.info("Authorisation code successfully exchanged and tokens saved.")
        return saved_data

    def check_and_refresh_token(self, force: bool = False) -> Dict[str, any]:
        """
        Check if the current authorisation token is nearing expiry and refresh it if so.

        Args:
            force: If True, bypasses expiry checks and forces token refreshment.

        Returns:
            The dictionary containing current valid token data.
        """
        token_data = self._load_token_storage()
        
        if not token_data:
            raise XeroAuthorisationError(
                "No token storage found. Please perform initial authorisation first."
            )

        expiry_timestamp = token_data.get("expiry_timestamp", 0)
        refresh_token = token_data.get("refresh_token")
        
        if not refresh_token:
            raise XeroAuthorisationError(
                "Refresh token is missing from token storage. Cannot refresh authorisation."
            )

        current_time = int(time.time())
        time_remaining = expiry_timestamp - current_time

        if force or time_remaining <= self.expiry_threshold_seconds:
            if force:
                logger.info("Forced token refreshment requested.")
            else:
                logger.info(
                    "Authorisation token is nearing expiry (%d seconds remaining). "
                    "Initiating token refresh...",
                    time_remaining
                )
            
            payload = {
                "grant_type": "refresh_token",  # Standard OAuth payload parameter
                "refresh_token": refresh_token,
            }
            
            new_token_data = self._make_token_request(payload)
            token_data = self._process_and_save_token_response(new_token_data)
            logger.info("Tokens refreshed successfully.")
        else:
            logger.info(
                "Authorisation token is valid. %d seconds remaining before refresh threshold.",
                time_remaining - self.expiry_threshold_seconds
            )

        return token_data

    def get_authorisation_token(self) -> str:
        """
        Convenience method to get a valid authorisation token.
        It automatically checks for expiry and refreshes the token if necessary.

        Returns:
            The valid access (authorisation) token.
        """
        valid_tokens = self.check_and_refresh_token()
        return valid_tokens["authorisation_token"]

    def _make_token_request(self, payload: Dict[str, str]) -> Dict[str, any]:
        """
        Perform the HTTP POST request to exchange or refresh tokens.

        Args:
            payload: Form fields to include in the POST body.

        Returns:
            The parsed JSON response dict from the server.
        """
        # Basic authentication header encoding client credentials
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            response = requests.post(
                XERO_TOKEN_ENDPOINT,
                data=payload,
                headers=headers,
                timeout=15
            )
            
            # Catch HTTP status errors
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as error:
            logger.error("HTTP request to Xero token endpoint failed: %s", error)
            # Try to extract detailed error details from response body if available
            error_details = ""
            if error.response is not None:
                try:
                    error_details = f" - Response body: {error.response.text}"
                except Exception:
                    pass
            raise XeroAuthorisationError(
                f"Failed to communicate with Xero token endpoint: {error}{error_details}"
            ) from error

    def _process_and_save_token_response(self, token_data: Dict[str, any]) -> Dict[str, any]:
        """
        Map the API response to UK English fields and write to storage.

        Args:
            token_data: Raw token data dictionary returned by Xero API.

        Returns:
            The mapped dictionary stored in the file.
        """
        # Read parameters from Xero API response
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        
        if not access_token or not refresh_token:
            raise XeroAuthorisationError("Invalid token response from Xero API.")

        # Calculate exact expiry timestamp
        current_time = int(time.time())
        expiry_timestamp = current_time + int(expires_in)

        # Map to UK English dictionary keys
        mapped_data = {
            "authorisation_token": access_token,
            "refresh_token": refresh_token,
            "expiry_timestamp": expiry_timestamp,
            "token_type": token_data.get("token_type", "Bearer"),
            "scopes": token_data.get("scope", "")
        }

        self._save_token_storage(mapped_data)
        return mapped_data

    def _load_token_storage(self) -> Optional[Dict[str, any]]:
        """
        Load cached tokens from the local JSON storage file.

        Returns:
            A dictionary containing token data, or None if the file is missing
            or contains invalid data.
        """
        if not os.path.exists(self.token_storage_path):
            logger.warning(
                "Token storage file not found at: %s. Re-authorisation is required.",
                self.token_storage_path
            )
            return None

        try:
            with open(self.token_storage_path, "r", encoding="utf-8") as storage_file:
                data = json.load(storage_file)

            # Basic validation check for expected keys
            required_keys = ["authorisation_token", "refresh_token", "expiry_timestamp"]
            if not all(key in data for key in required_keys):
                logger.error(
                    "Token storage file is corrupted or contains invalid format."
                )
                return None

            return data

        except (json.JSONDecodeError, OSError) as error:
            logger.error(
                "Failed to read or parse token storage file: %s. Re-authorisation required.",
                error
            )
            return None

    def _save_token_storage(self, data: Dict[str, any]) -> None:
        """
        Save the token data to the local JSON storage file.
        Attempts to restrict file permissions on UNIX-like environments.

        Args:
            data: Mapped token dictionary to save.
        """
        try:
            # Serialise and write to the local file
            with open(self.token_storage_path, "w", encoding="utf-8") as storage_file:
                json.dump(data, storage_file, indent=4)

            # Attempt to set secure permissions (owner read/write only) on POSIX
            if os.name == "posix":
                os.chmod(self.token_storage_path, 0o600)
                
            logger.info("Tokens successfully saved to storage.")
            
        except OSError as error:
            logger.error("Failed to write to token storage file: %s", error)
            raise XeroAuthorisationError(
                f"Could not persist tokens to local storage: {error}"
            ) from error
