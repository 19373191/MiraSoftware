"""
Unit tests for the new M.I.R.A. Dashboard API endpoints in app.py.

Verifies status indicators, logs collectors, sync pipeline launchers, and auth redirects.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

import app as flask_app


class TestAppDashboardAPI(unittest.TestCase):
    """Test suite for app.py dashboard integration API endpoints."""

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        # Reset log buffer before each test
        flask_app.log_handler.logs.clear()

    @patch("app.XeroAuthorisationManager.get_authorisation_token")
    def test_get_status_active(self, mock_get_token):
        """Verify get_status returns True connection flags when token is valid."""
        mock_get_token.return_value = {"access_token": "token_123"}
        
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data.decode("utf-8"))
        self.assertTrue(data["monday_connected"])
        self.assertTrue(data["xero_connected"])

    @patch("app.XeroAuthorisationManager.get_authorisation_token", side_effect=Exception("No token cached"))
    @patch.dict("os.environ", {"XERO_DRY_RUN": "False"})
    def test_get_status_inactive(self, mock_get_token):
        """Verify get_status returns False connection flag when Xero token is missing."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.data.decode("utf-8"))
        self.assertFalse(data["xero_connected"])

    @patch("app.XeroAuthorisationManager.generate_authorisation_url")
    def test_auth_xero_redirect(self, mock_generate_url):
        """Verify auth_xero generates url and redirects browser client."""
        mock_generate_url.return_value = "https://login.xero.com/consent"
        
        response = self.client.get("/auth/xero")
        self.assertEqual(response.status_code, 302)  # Flask redirect code 302
        self.assertEqual(response.headers["Location"], "https://login.xero.com/consent")

    def test_get_and_clear_logs(self):
        """Verify logs collector appends entries and clears them via DELETE request."""
        # Append mock logs directly to log_handler
        flask_app.log_handler.logs.append({
            "timestamp": 1234567.0,
            "level": "INFO",
            "logger": "TestLogger",
            "message": "Test log entry"
        })

        # Fetch logs
        response = self.client.get("/api/logs")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(len(data["logs"]), 1)
        self.assertEqual(data["logs"][0]["message"], "Test log entry")

        # Clear logs
        delete_response = self.client.delete("/api/logs")
        self.assertEqual(delete_response.status_code, 200)
        
        # Check logs are cleared
        response_cleared = self.client.get("/api/logs")
        data_cleared = json.loads(response_cleared.data.decode("utf-8"))
        self.assertEqual(len(data_cleared["logs"]), 0)

    @patch("app.threading.Thread")
    def test_run_sync_starts_thread(self, mock_thread):
        """Verify run_sync fires a background sync executor thread."""
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        payload = {"status": "DRAFT", "chunk_size": 25}
        response = self.client.post(
            "/api/run-sync",
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")
        
        # Verify thread was initialized and started
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()


    @patch("app.config_manager.load_mapping")
    def test_get_mapping(self, mock_load):
        """Verify GET /api/config returns active configurations."""
        mock_load.return_value = {"transaction_id": "Ref"}
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["transaction_id"], "Ref")

    @patch("app.config_manager.save_mapping")
    def test_post_mapping_success(self, mock_save):
        """Verify POST /api/config persists valid configurations successfully."""
        mock_save.return_value = (True, [])
        payload = {"transaction_id": "Ref"}
        
        response = self.client.post(
            "/api/config",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["status"], "success")

    @patch("app.config_manager.save_mapping")
    def test_post_mapping_validation_failure(self, mock_save):
        """Verify POST /api/config yields HTTP 400 when validation checks fail."""
        mock_save.return_value = (False, ["Mandatory field missing"])
        payload = {"transaction_id": ""}
        
        response = self.client.post(
            "/api/config",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["status"], "error")
        self.assertIn("Mandatory field missing", data["errors"])


if __name__ == "__main__":
    unittest.main()
