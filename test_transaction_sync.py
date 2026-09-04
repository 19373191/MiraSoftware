"""
Unit tests for XeroTransactionSync module.

Mocks the Xero API endpoints to verify connection parsing, transaction-to-invoice
schema mapping, and chunked batching mechanisms.
"""

import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import requests
from transaction_sync import XeroTransactionSync, XeroSyncError, XERO_CONNECTIONS_ENDPOINT, XERO_INVOICES_ENDPOINT
from auth_manager import XeroAuthorisationManager


class TestXeroTransactionSync(unittest.TestCase):
    """Test suite for the XeroTransactionSync class."""

    def setUp(self):
        # Mock auth manager
        self.mock_auth = MagicMock(spec=XeroAuthorisationManager)
        self.mock_auth.get_authorisation_token.return_value = "mocked_bearer_token"
        
        self.sync_manager = XeroTransactionSync(self.mock_auth)

        # Realistic mock transaction record
        self.mock_transaction = {
            "transaction_id": "999aaabbb-ccc-ddd-eee-fff123456789",
            "customer_first_name": "Oliver",
            "customer_last_name": "Smith",
            "company_name": "UK Tech Supplies Ltd",
            "email_address": "oliver.smith@uktechsupplies.com",
            "item_description": "Enterprise SaaS Subscription (Annual)",
            "quantity": 2,
            "unit_price": 5000.00,
            "target_date": "2026-07-10T14:30:00Z"
        }

    def test_map_transaction_to_invoice(self):
        """Verify transaction fields map correctly to Xero's ACCREC invoice structure."""
        invoice = self.sync_manager.map_transaction_to_invoice(self.mock_transaction, status="DRAFT")

        self.assertEqual(invoice["Type"], "ACCREC")
        self.assertEqual(invoice["Status"], "DRAFT")
        self.assertEqual(invoice["Reference"], "TX-999aaabb")
        
        # Verify nested Contact schema
        self.assertEqual(invoice["Contact"]["Name"], "UK Tech Supplies Ltd")
        self.assertEqual(invoice["Contact"]["FirstName"], "Oliver")
        self.assertEqual(invoice["Contact"]["LastName"], "Smith")
        self.assertEqual(invoice["Contact"]["EmailAddress"], "oliver.smith@uktechsupplies.com")

        # Verify Date formats (Credit terms: Due date = Date + 30 days)
        self.assertEqual(invoice["Date"], "2026-07-10")
        self.assertEqual(invoice["DueDate"], "2026-08-09")  # 31 days in July, so 30 days from 10th is Aug 9th

        # Verify LineItems details
        self.assertEqual(len(invoice["LineItems"]), 1)
        line = invoice["LineItems"][0]
        self.assertEqual(line["Description"], "Enterprise SaaS Subscription (Annual)")
        self.assertEqual(line["Quantity"], 2)
        self.assertEqual(line["UnitAmount"], 5000.00)
        self.assertEqual(line["AccountCode"], "200")

    def test_map_transaction_invalid_date_fallback(self):
        """Verify invalid date string defaults to current date instead of failing."""
        invalid_tx = self.mock_transaction.copy()
        invalid_tx["target_date"] = "not-a-date"
        
        # Should not raise exception
        invoice = self.sync_manager.map_transaction_to_invoice(invalid_tx)
        self.assertIsNotNone(invoice["Date"])
        self.assertIsNotNone(invoice["DueDate"])

    @patch("transaction_sync.requests.get")
    def test_get_active_tenant_id_success(self, mock_get):
        """Verify correct tenant ID is extracted and cached from active connections list."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "id": "conn-uuid-1",
                "tenantId": "tenant-uuid-12345",
                "tenantName": "UK Testing Organisation",
                "tenantType": "ORGANISATION"
            }
        ]
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        tenant_id = self.sync_manager.get_active_tenant_id()
        
        # Verify request details
        mock_get.assert_called_once_with(XERO_CONNECTIONS_ENDPOINT, headers=unittest.mock.ANY, timeout=15)
        self.assertEqual(tenant_id, "tenant-uuid-12345")

        # Verify subsequent call returns cached ID without query
        mock_get.reset_mock()
        cached_id = self.sync_manager.get_active_tenant_id()
        mock_get.assert_not_called()
        self.assertEqual(cached_id, "tenant-uuid-12345")

    @patch("transaction_sync.requests.get")
    def test_get_active_tenant_id_empty(self, mock_get):
        """Verify error is raised if user has no connections (tenants)."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with self.assertRaises(XeroSyncError):
            self.sync_manager.get_active_tenant_id()

    @patch("transaction_sync.requests.post")
    @patch("transaction_sync.requests.get")
    def test_sync_batch_successful(self, mock_get, mock_post):
        """Verify batch transactions are mapped, chunked, and posted successfully."""
        # Mock connections API for tenant resolution
        mock_conn = MagicMock()
        mock_conn.json.return_value = [{"tenantId": "tenant_abc", "tenantName": "Org Name"}]
        mock_conn.status_code = 200
        mock_get.return_value = mock_conn

        # Mock invoice post API
        mock_inv = MagicMock()
        mock_inv.json.return_value = {
            "Invoices": [
                {"InvoiceID": "inv-1", "Reference": "TX-a"},
                {"InvoiceID": "inv-2", "Reference": "TX-b"}
            ]
        }
        mock_inv.status_code = 200
        mock_post.return_value = mock_inv

        # Setup 4 items to trigger chunking with size=2
        transactions = [self.mock_transaction] * 4
        
        result = self.sync_manager.sync_batch(transactions, status="DRAFT", chunk_size=2)

        # Assert correct count of successful items
        # 4 items with chunk size 2 means 2 post operations, returning 2 invoices each
        self.assertEqual(result["successful_count"], 4)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(len(result["details"]), 4)
        self.assertEqual(mock_post.call_count, 2)

    @patch("transaction_sync.requests.post")
    @patch("transaction_sync.requests.get")
    def test_sync_batch_partial_failures(self, mock_get, mock_post):
        """Verify chunk errors do not abort the entire batch flow."""
        mock_conn = MagicMock()
        mock_conn.json.return_value = [{"tenantId": "tenant_abc"}]
        mock_conn.status_code = 200
        mock_get.return_value = mock_conn

        # Mock first POST to succeed, second to fail
        response_success = MagicMock()
        response_success.json.return_value = {"Invoices": [{"InvoiceID": "inv-1"}]}
        response_success.status_code = 200

        # Simulate exception in second POST
        mock_post.side_effect = [response_success, requests.exceptions.HTTPError("Internal Server Error")]

        # Setup 4 items to chunk with size=2
        transactions = [self.mock_transaction] * 4
        
        result = self.sync_manager.sync_batch(transactions, status="DRAFT", chunk_size=2)

        # Verify results: chunk 1 succeeded (2 items), chunk 2 failed (2 items)
        self.assertEqual(result["successful_count"], 1)  # Mock returns only 1 invoice
        self.assertEqual(result["failed_count"], 2)  # Chunk 2 failed completely
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
