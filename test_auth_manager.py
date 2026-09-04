"""
Unit tests for XeroAuthorisationManager.

This test file verifies the authentication and token management logic by mocking the
external network calls to the Xero API.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import requests
from auth_manager import (
    XeroAuthorisationManager,
    XeroAuthorisationError,
    XERO_AUTHORISATION_ENDPOINT,
    XERO_TOKEN_ENDPOINT
)


class TestXeroAuthorisationManager(unittest.TestCase):
    """Test suite for the XeroAuthorisationManager class."""

    def setUp(self):
        """Set up standard client configuration and temporary storage file."""
        self.client_id = "test_client_id_123"
        self.client_secret = "test_client_secret_xyz"
        self.redirect_uri = "https://localhost"
        
        # Create a temporary file for token storage to avoid modifying real files
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.temp_file.close()
        self.token_storage_path = self.temp_file.name

        self.manager = XeroAuthorisationManager(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            token_storage_path=self.token_storage_path,
            expiry_threshold_seconds=300
        )

    def tearDown(self):
        """Clean up the temporary storage file."""
        if os.path.exists(self.token_storage_path):
            os.remove(self.token_storage_path)

    def test_initialise_with_missing_credentials(self):
        """Verify initialization raises ValueError if credentials are missing."""
        with self.assertRaises(ValueError):
            XeroAuthorisationManager(client_id="", client_secret="secret")
        with self.assertRaises(ValueError):
            XeroAuthorisationManager(client_id="client", client_secret="")

    def test_generate_authorisation_url(self):
        """Verify authorization URL is generated with correct parameters."""
        state = "random_state_string_456"
        scope = "openid profile accounting.transactions offline_access"
        
        url = self.manager.generate_authorisation_url(scope=scope, state=state)
        
        self.assertTrue(url.startswith(XERO_AUTHORISATION_ENDPOINT))
        self.assertIn(f"client_id={self.client_id}", url)
        self.assertIn("redirect_uri=https%3A%2F%2Flocalhost", url)
        self.assertIn("scope=openid+profile+accounting.transactions+offline_access", url)
        self.assertIn(f"state={state}", url)

    @patch("auth_manager.requests.post")
    def test_exchange_authorisation_code_success(self, mock_post):
        """Verify successful exchange of authorization code for tokens."""
        # Mock successful token response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "mock_access_token_abc",
            "refresh_token": "mock_refresh_token_def",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "openid offline_access"
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        authorisation_code = "auth_code_999"
        result = self.manager.exchange_authorisation_code(authorisation_code)

        # Assert POST request details
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], XERO_TOKEN_ENDPOINT)
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(kwargs["data"]["code"], authorisation_code)
        self.assertEqual(kwargs["data"]["redirect_uri"], self.redirect_uri)
        self.assertIn("Authorization", kwargs["headers"])
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Basic "))

        # Assert result keys map to UK English
        self.assertEqual(result["authorisation_token"], "mock_access_token_abc")
        self.assertEqual(result["refresh_token"], "mock_refresh_token_def")
        self.assertEqual(result["token_type"], "Bearer")
        
        # Verify saved token content
        with open(self.token_storage_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["authorisation_token"], "mock_access_token_abc")
        self.assertEqual(saved_data["refresh_token"], "mock_refresh_token_def")
        self.assertTrue(saved_data["expiry_timestamp"] > int(time.time()))

    @patch("auth_manager.requests.post")
    def test_exchange_authorisation_code_http_error(self, mock_post):
        """Verify HTTP errors in code exchange raise XeroAuthorisationError."""
        mock_post.side_effect = requests.exceptions.HTTPError("Bad Request")
        
        with self.assertRaises(XeroAuthorisationError):
            self.manager.exchange_authorisation_code("invalid_code")

    def test_check_and_refresh_token_missing_storage(self):
        """Verify check_and_refresh_token raises error if storage file is missing."""
        # Delete temp file to ensure it's missing
        if os.path.exists(self.token_storage_path):
            os.remove(self.token_storage_path)
            
        with self.assertRaises(XeroAuthorisationError):
            self.manager.check_and_refresh_token()

    def test_check_and_refresh_token_corrupt_storage(self):
        """Verify corrupted JSON storage is handled gracefully and returns error on refresh."""
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            f.write("invalid-json-content")
            
        with self.assertRaises(XeroAuthorisationError):
            self.manager.check_and_refresh_token()

    def test_check_and_refresh_token_missing_keys(self):
        """Verify storage JSON with missing keys is treated as invalid."""
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            json.dump({"authorisation_token": "only_token_here"}, f)
            
        with self.assertRaises(XeroAuthorisationError):
            self.manager.check_and_refresh_token()

    @patch("auth_manager.requests.post")
    def test_check_and_refresh_token_valid(self, mock_post):
        """Verify token is NOT refreshed if it is still valid and not nearing expiry."""
        current_time = int(time.time())
        token_data = {
            "authorisation_token": "valid_token_123",
            "refresh_token": "valid_refresh_token_456",
            "expiry_timestamp": current_time + 600,  # 10 minutes remaining (threshold is 300)
            "token_type": "Bearer",
            "scopes": "openid offline_access"
        }
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        # Execute check
        result = self.manager.check_and_refresh_token()
        
        # Verify no POST request was made since token is valid
        mock_post.assert_not_called()
        self.assertEqual(result["authorisation_token"], "valid_token_123")

    @patch("auth_manager.requests.post")
    def test_check_and_refresh_token_nearing_expiry(self, mock_post):
        """Verify token IS refreshed when remaining life is below threshold."""
        current_time = int(time.time())
        token_data = {
            "authorisation_token": "old_token",
            "refresh_token": "my_refresh_token",
            "expiry_timestamp": current_time + 100,  # 100 seconds left (threshold is 300)
            "token_type": "Bearer",
            "scopes": "openid offline_access"
        }
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        # Mock API response for refresh
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token_789",
            "refresh_token": "new_refresh_token_abc",
            "expires_in": 1800,
            "token_type": "Bearer"
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Execute check (should trigger refresh)
        result = self.manager.check_and_refresh_token()

        # Verify POST request was made to refresh token
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(kwargs["data"]["refresh_token"], "my_refresh_token")

        # Verify stored and returned data reflects new values
        self.assertEqual(result["authorisation_token"], "new_token_789")
        self.assertEqual(result["refresh_token"], "new_refresh_token_abc")
        
        with open(self.token_storage_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["authorisation_token"], "new_token_789")
        self.assertTrue(saved_data["expiry_timestamp"] > current_time + 1700)

    @patch("auth_manager.requests.post")
    def test_check_and_refresh_token_forced(self, mock_post):
        """Verify token is refreshed when force=True is passed regardless of expiry."""
        current_time = int(time.time())
        token_data = {
            "authorisation_token": "valid_token",
            "refresh_token": "my_refresh_token",
            "expiry_timestamp": current_time + 1000,  # 1000 seconds left (valid)
            "token_type": "Bearer",
            "scopes": "openid offline_access"
        }
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        # Mock API response for refresh
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token_789",
            "refresh_token": "new_refresh_token_abc",
            "expires_in": 1800,
            "token_type": "Bearer"
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Force refresh
        result = self.manager.check_and_refresh_token(force=True)

        mock_post.assert_called_once()
        self.assertEqual(result["authorisation_token"], "new_token_789")

    @patch("auth_manager.requests.post")
    def test_get_authorisation_token(self, mock_post):
        """Verify get_authorisation_token returns the access token directly."""
        current_time = int(time.time())
        token_data = {
            "authorisation_token": "direct_token_val",
            "refresh_token": "my_refresh_token",
            "expiry_timestamp": current_time + 1000,
            "token_type": "Bearer"
        }
        with open(self.token_storage_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        token = self.manager.get_authorisation_token()
        self.assertEqual(token, "direct_token_val")


if __name__ == "__main__":
    unittest.main()
