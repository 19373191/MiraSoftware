"""
Unit tests for the Project M.I.R.A. Flask Orchestration app.

Tests route accessibility, security auth token validations, the Monday.com
verification handshake, and mock runs of the extraction-transformation-loading pipeline.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

import app as flask_app
from auth_manager import XeroAuthorisationError
from transaction_sync import XeroSyncError


class TestProjectMIRAOrchestrator(unittest.TestCase):
    """Test suite for the central app.py orchestration webhook server."""

    def setUp(self):
        # Configure Flask test client
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        
        # Test configurations
        self.valid_token = flask_app.MONDAY_WEBHOOK_TOKEN
        self.auth_headers = {"Authorization": f"Bearer {self.valid_token}"}
        
        # Mock incoming transaction data (Monday.com webhook simulation payload)
        self.incoming_payload = {
            "transaction_id": "tx_webhook_999",
            "customer_first_name": "Oliver",
            "customer_last_name": "Smith",
            "company_name": "UK Tech Supplies Ltd",
            "email_address": "oliver.smith@uktechsupplies.com",
            "item_description": "Enterprise SaaS Subscription (Annual)",
            "quantity": 2,
            "unit_price": 5000.00,
            "target_date": "2026-07-10T14:30:00Z"
        }

    def test_health_check(self):
        """Verify the health status endpoint responds correctly."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["project"], "M.I.R.A.")

    def test_monday_challenge_handshake(self):
        """Verify Monday.com webhook challenge handshake resolves successfully."""
        challenge_token = "random_challenge_verification_code_123"
        challenge_payload = {"challenge": challenge_token}
        
        # Handshake requests do not require Authorization header
        response = self.client.post(
            "/webhook/monday",
            json=challenge_payload
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["challenge"], challenge_token)

    def test_webhook_unauthorised_missing_header(self):
        """Verify request is rejected with 401 when Authorization header is missing."""
        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    def test_webhook_unauthorised_invalid_token(self):
        """Verify request is rejected with 401 when Authorization token is invalid."""
        invalid_headers = {"Authorization": "Bearer invalid_secret_token_111"}
        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload,
            headers=invalid_headers
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    @patch("app.get_integration_components")
    def test_webhook_pipeline_success(self, mock_get_components):
        """Verify full ETL pipeline success maps, posts and returns HTTP 201 with Xero ID."""
        # Setup mocks
        mock_auth = MagicMock()
        mock_transformer = MagicMock()
        mock_sync = MagicMock()
        mock_get_components.return_value = (mock_auth, mock_transformer, mock_sync)

        # Mock Stage 2: Transformation returns success
        mock_transformer.transform_single.return_value = {
            "status": "success",
            "invoice": {"Type": "ACCREC", "Reference": "TX-1"}
        }

        # Mock Stage 3: Loading returns tenant ID and invoices response
        mock_sync.get_active_tenant_id.return_value = "tenant_id_xyz"
        mock_sync._post_invoices_to_xero.return_value = {
            "Invoices": [{"InvoiceID": "xero_invoice_id_888"}]
        }

        # Execute webhook trigger
        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload,
            headers=self.auth_headers
        )

        # Asserts
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["xero_invoice_id"], "xero_invoice_id_888")
        
        # Verify stages were called in correct sequence
        mock_transformer.transform_single.assert_called_once_with(self.incoming_payload)
        mock_sync.get_active_tenant_id.assert_called_once()
        mock_sync._post_invoices_to_xero.assert_called_once_with("tenant_id_xyz", [{"Type": "ACCREC", "Reference": "TX-1"}])

    @patch("app.get_integration_components")
    def test_webhook_pipeline_transformation_failed(self, mock_get_components):
        """Verify validation failure at transformation stage returns HTTP 422."""
        mock_auth = MagicMock()
        mock_transformer = MagicMock()
        mock_sync = MagicMock()
        mock_get_components.return_value = (mock_auth, mock_transformer, mock_sync)

        # Mock Stage 2: Transformation validation failure
        mock_transformer.transform_single.return_value = {
            "status": "validation_failed",
            "validation_errors": ["Invalid email format", "Quantity must be positive"]
        }

        # Execute webhook trigger
        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload,
            headers=self.auth_headers
        )

        # Asserts
        self.assertEqual(response.status_code, 422)
        data = response.get_json()
        self.assertEqual(data["status"], "transformation_failed")
        self.assertEqual(len(data["errors"]), 2)
        
        # Verify Loading stage was skipped
        mock_sync._post_invoices_to_xero.assert_not_called()

    @patch("app.get_integration_components")
    def test_webhook_pipeline_auth_expired(self, mock_get_components):
        """Verify Xero authentication errors in Loading stage return HTTP 403."""
        mock_auth = MagicMock()
        mock_transformer = MagicMock()
        mock_sync = MagicMock()
        mock_get_components.return_value = (mock_auth, mock_transformer, mock_sync)

        mock_transformer.transform_single.return_value = {
            "status": "success",
            "invoice": {"Type": "ACCREC"}
        }

        # Simulate authentication expiry exception in loading phase
        mock_sync.get_active_tenant_id.side_effect = XeroAuthorisationError("Refresh token expired")

        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload,
            headers=self.auth_headers
        )

        # Asserts
        self.assertEqual(response.status_code, 403)
        data = response.get_json()
        self.assertEqual(data["status"], "loading_failed")
        self.assertIn("authentication", data["message"])

    @patch("app.get_integration_components")
    def test_webhook_pipeline_sync_api_error(self, mock_get_components):
        """Verify Xero synchronisation errors in Loading stage return HTTP 502."""
        mock_auth = MagicMock()
        mock_transformer = MagicMock()
        mock_sync = MagicMock()
        mock_get_components.return_value = (mock_auth, mock_transformer, mock_sync)

        mock_transformer.transform_single.return_value = {
            "status": "success",
            "invoice": {"Type": "ACCREC"}
        }

        mock_sync.get_active_tenant_id.return_value = "tenant_id_xyz"
        mock_sync._post_invoices_to_xero.side_effect = XeroSyncError("Xero API rate limit exceeded")

        response = self.client.post(
            "/webhook/monday",
            json=self.incoming_payload,
            headers=self.auth_headers
        )

        # Asserts
        self.assertEqual(response.status_code, 502)
        data = response.get_json()
        self.assertEqual(data["status"], "loading_failed")
        self.assertIn("Xero API", data["message"])


if __name__ == "__main__":
    unittest.main()
