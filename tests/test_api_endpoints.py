"""
Unit tests for the new FastAPI endpoints in M.I.R.A.
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app
from models.db import User, Base
from utils.auth import create_access_token

class TestFastAPIEndpoints(unittest.TestCase):
    """Test suite for new FastAPI endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        # Create a mock token for current user dependency
        self.token = create_access_token({"sub": "admin@mira.local", "user_id": 1, "role": "admin"})
        self.headers = {"Cookie": f"mira_access_token={self.token}"}

    def test_get_health(self):
        """Verify health check endpoint returns success."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    @patch("routes.sync.get_user_credentials")
    def test_get_api_status(self, mock_get_creds):
        """Verify API status connector flags are returned."""
        mock_get_creds.return_value = MagicMock(api_key="key_123", access_token="tok_123", token_expiry=9999999999.0)
        response = self.client.get("/api/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("monday_connected", data)
        self.assertIn("xero_connected", data)

    def test_api_config_get_and_post(self):
        """Verify API configuration load and save operations."""
        # Test config GET
        get_response = self.client.get("/api/config", headers=self.headers)
        self.assertEqual(get_response.status_code, 200)
        get_data = get_response.json()
        self.assertIn("board_id_1", get_data)
        self.assertIn("mappings", get_data)

        # Test config POST
        payload = {
            "board_id_1": "board_one",
            "mappings": [
                {
                    "target_xero_path": "Invoice.Reference",
                    "board_1_col": "transaction_id",
                    "custom_override_path": "TX-{ref}"
                }
            ]
        }
        post_response = self.client.post("/api/config", json=payload, headers=self.headers)
        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(post_response.json()["status"], "success")

    def test_api_logs_and_run_sync(self):
        """Verify log polling, deletion, and background sync submission."""
        # Clear logs
        del_res = self.client.delete("/api/logs", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)

        # Fetch logs (should be empty)
        get_res = self.client.get("/api/logs", headers=self.headers)
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(len(get_res.json()["logs"]), 0)

        # Trigger sync
        sync_payload = {
            "direction": "xero_to_monday",
            "board_id_1": "default",
            "batch_count": 10,
            "group_by_company": True,
            "target_account": "200"
        }
        run_res = self.client.post("/api/run-sync", json=sync_payload, headers=self.headers)
        self.assertEqual(run_res.status_code, 202)
        self.assertEqual(run_res.json()["status"], "success")

    def test_board_mappings_details(self):
        """Verify the board mappings columns and details endpoints work."""
        # Test columns endpoint
        col_res = self.client.get("/api/mappings/board/default/columns", headers=self.headers)
        self.assertEqual(col_res.status_code, 200)
        self.assertIn("columns", col_res.json())

        # Test details endpoint
        details_res = self.client.get("/api/mappings/board/default/details", headers=self.headers)
        self.assertEqual(details_res.status_code, 200)
        data = details_res.json()
        self.assertIn("columns", data)
        self.assertIn("mappings", data)

    def test_operator_login_redirect(self):
        """Verify that an operator login redirects to /sync instead of /connectors."""
        from models.db import User, get_db
        from utils.auth import hash_password

        # Mock database session
        mock_db = MagicMock()

        # Create a mock operator user (operator is role="user" in the db)
        mock_operator = User(
            id=2,
            email="operator@mira.local",
            hashed_password=hash_password("OperatorPass123!"),
            role="user",
            is_active=True
        )
        mock_db.query.return_value.filter.return_value.first.return_value = mock_operator

        # Set up dependency override
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            # Request login via POST Form data
            response = self.client.post(
                "/login",
                data={"email": "operator@mira.local", "password": "OperatorPass123!"},
                follow_redirects=False
            )
            
            # Verify redirect to /sync
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["Location"], "/sync")
        finally:
            app.dependency_overrides.clear()



