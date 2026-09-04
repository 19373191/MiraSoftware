"""
Unit tests for M.I.R.A. Connector Modules and OAuth Handler.
"""

import unittest
from unittest.mock import MagicMock, patch
from models.schemas import Contact, Product, LineItem, Invoice
from utils.oauth_handler import OAuthHandler
from connectors.xero_connector import XeroConnector
from connectors.monday_connector import MondayConnector


class TestConnectors(unittest.TestCase):
    """Test suite for Xero and Monday connectors and OAuth handler."""

    def setUp(self):
        self.oauth_handler = OAuthHandler(
            client_id="test_client_id",
            client_secret="test_client_secret",
            redirect_uri="https://localhost",
        )

    def test_oauth_authorization_url(self):
        auth_url = self.oauth_handler.get_authorization_url(state="state_123")
        self.assertIn("https://login.xero.com/identity/connect/authorize", auth_url)
        self.assertIn("client_id=test_client_id", auth_url)
        self.assertIn("redirect_uri=https%3A%2F%2Flocalhost", auth_url)

    @patch("requests.post")
    def test_oauth_token_exchange_and_auto_refresh(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "mock_access_token_123",
            "refresh_token": "mock_refresh_token_456",
            "expires_in": 1800,
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        data = self.oauth_handler.exchange_code_for_token("auth_code_xyz")
        self.assertEqual(data["access_token"], "mock_access_token_123")
        self.assertFalse(self.oauth_handler.is_token_expired())

        headers = self.oauth_handler.get_bearer_header()
        self.assertEqual(headers["Authorization"], "Bearer mock_access_token_123")

    @patch("requests.post")
    def test_xero_connector_endpoints(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"Status": "OK"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        xero = XeroConnector(tenant_id="tenant_123", oauth_handler=self.oauth_handler)
        self.oauth_handler._access_token = "valid_token"
        self.oauth_handler._expires_at = 9999999999.0

        # Test create_or_update_contact
        contact = Contact(name="Cyberdyne", email="sales@cyberdyne.com")
        res_contact = xero.create_or_update_contact(contact)
        self.assertEqual(res_contact, {"Status": "OK"})

        # Test create_product_item
        product = Product(code="SKU-CPU", name="Neural Processor", unit_price=1500.0)
        res_product = xero.create_product_item(product)
        self.assertEqual(res_product, {"Status": "OK"})

        # Test create_invoice
        line_item = LineItem(item_code="SKU-CPU", quantity=1, unit_amount=1500.0)
        invoice = Invoice(
            contact=contact,
            line_items=[line_item],
            issue_date="2026-08-01",
            due_date="2026-08-31",
        )
        res_invoice = xero.create_invoice(invoice)
        self.assertEqual(res_invoice, {"Status": "OK"})

    @patch("requests.post")
    def test_monday_connector_operations(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "boards": [
                    {
                        "id": "100",
                        "items_page": {
                            "items": [
                                {"id": "item_1", "name": "Task 1", "column_values": []}
                            ]
                        },
                    }
                ]
            }
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        monday = MondayConnector(api_key="test_api_key", board_id="100")

        # Test query_board_items
        items = monday.query_board_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "item_1")

        # Test update_item_status
        mock_response.json.return_value = {
            "data": {"change_simple_column_value": {"id": "item_1", "name": "Task 1"}}
        }
        res_status = monday.update_item_status("item_1", "Invoiced")
        self.assertIn("change_simple_column_value", res_status.get("data", {}))

        # Test post_item_update
        mock_response.json.return_value = {
            "data": {"create_update": {"id": "update_99"}}
        }
        res_update = monday.post_item_update("item_1", "Invoiced successfully in Xero.")
        self.assertIn("create_update", res_update.get("data", {}))


if __name__ == "__main__":
    unittest.main()
