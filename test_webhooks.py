"""
Unit tests for M.I.R.A. Real-Time Webhook Endpoints in main.py.
"""

from unittest.mock import MagicMock, patch
import unittest

try:
    import fastapi
    has_fastapi = True
except ImportError:
    has_fastapi = False

from main import app, _process_monday_item_changed
from models.schemas import Contact, Product, LineItem, Invoice


class TestWebhooks(unittest.TestCase):
    """Test suite for Monday and Xero real-time webhook handlers."""

    def setUp(self):
        if not has_fastapi:
            self.skipTest("FastAPI module not installed in current Python environment.")

    @patch("main.monday_connector")
    @patch("main.xero_connector")
    def test_monday_item_changed_success(self, mock_xero, mock_monday):
        mock_monday.query_board_items.return_value = [
            {
                "id": "12345",
                "name": "Wayne Enterprises Deal",
                "column_values": [
                    {"id": "company", "text": "Wayne Enterprises"},
                    {"id": "email", "text": "bruce@wayne.com"},
                    {"id": "price", "text": "5000.0"},
                    {"id": "quantity", "text": "1"},
                ],
            }
        ]
        mock_xero.create_or_update_contact.return_value = {"Status": "OK"}
        mock_xero.create_product_item.return_value = {"Status": "OK"}
        mock_xero.create_invoice.return_value = {
            "Invoices": [{"InvoiceNumber": "INV-WAYNE-001"}]
        }

        payload = {"event": {"pulseId": 12345, "boardId": 999}}
        result = _process_monday_item_changed(payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["xero_invoice_number"], "INV-WAYNE-001")
        mock_monday.update_item_status.assert_called_with("12345", "Synced to Xero", board_id="999")
        mock_monday.post_item_update.assert_called()

    @patch("main.monday_connector")
    @patch("main.xero_connector")
    def test_monday_item_changed_validation_error(self, mock_xero, mock_monday):
        # Missing required email address in payload
        mock_monday.query_board_items.return_value = [
            {
                "id": "12346",
                "name": "Incomplete Deal",
                "column_values": [
                    {"id": "company", "text": "Incomplete Inc"},
                ],
            }
        ]

        payload = {"event": {"pulseId": 12346, "boardId": 999}}
        result = _process_monday_item_changed(payload)

        self.assertEqual(result["status"], "error")
        self.assertIn("Validation Error", result["error"])
        mock_monday.update_item_status.assert_called_with("12346", "Sync Error", board_id="999")
        mock_monday.post_item_update.assert_called()

    @patch("main.monday_connector")
    def test_xero_invoice_updated_paid(self, mock_monday):
        from main import xero_invoice_updated_webhook
        import asyncio

        payload = {
            "events": [
                {
                    "resourceId": "INV-10099",
                    "status": "PAID",
                    "reference": "12345",
                }
            ]
        }
        res = asyncio.run(xero_invoice_updated_webhook(payload))

        self.assertEqual(res["status"], "success")
        self.assertIn("12345", res["updated_monday_items"])
        mock_monday.update_item_status.assert_called_with("12345", "Paid")
        mock_monday.post_item_update.assert_called()


if __name__ == "__main__":
    unittest.main()
