"""
Unit tests for the M.I.R.A. Dashboard routes in app.py.

Verifies that the dashboard index.html is served successfully at GET /.
"""

import unittest
from unittest.mock import patch, mock_open

import app as flask_app


class TestDashboardRoute(unittest.TestCase):
    """Test suite for the UI Dashboard route."""

    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()

    def test_serve_dashboard_success(self):
        """Verify GET / resolves successfully and returns HTML containing project labels."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        
        # Verify response matches index.html content (contains M.I.R.A. labels)
        html_content = response.data.decode("utf-8")
        self.assertIn("Project M.I.R.A.", html_content)
        self.assertIn("Platform Connections", html_content)
        self.assertIn("Run Sync Engine", html_content)
        self.assertIn("System Terminal", html_content)

    @patch("builtins.open", side_effect=OSError("File not found"))
    def test_serve_dashboard_file_missing(self, mock_file_open):
        """Verify response returns 404 error if index.html is missing."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.data.decode("utf-8").lower())


    def test_serve_js_success(self):
        """Verify GET /app.js resolves successfully and returns JS content type."""
        response = self.client.get("/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/javascript")
        self.assertIn("DOMContentLoaded", response.data.decode("utf-8"))

    @patch("builtins.open", side_effect=OSError("File not found"))
    def test_serve_js_file_missing(self, mock_file_open):
        """Verify response returns 404 error if app.js is missing."""
        response = self.client.get("/app.js")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.data.decode("utf-8").lower())


if __name__ == "__main__":
    unittest.main()
