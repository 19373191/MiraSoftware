"""
Unit tests for M.I.R.A. Data Transformation Engine and Schemas.
"""

import unittest
from models.schemas import Contact, Product, LineItem, Invoice
from engine.transformer import (
    monday_item_to_xero_contact,
    monday_item_to_xero_product,
    monday_items_to_xero_invoice,
    TransformerValidationError,
)


class TestTransformerEngine(unittest.TestCase):
    """Test suite for Monday to Xero transformation & validation engine."""

    def test_monday_item_to_xero_contact_success(self):
        payload = {
            "transaction_id": "TX-001",
            "company_name": "Acme Holdings",
            "customer_first_name": "Edward",
            "customer_last_name": "Smith",
            "email_address": "billing@acme.com",
            "phone": "+44 20 7946 0912",
            "account_number": "ACC-789",
            "contact_id": "CON-100",
            "address": "123 Industrial Way, London, EC1A 1BB",
        }
        contact = monday_item_to_xero_contact(payload)
        self.assertIsInstance(contact, Contact)
        self.assertEqual(contact.Name, "Acme Holdings")
        self.assertEqual(contact.Email, "billing@acme.com")
        self.assertEqual(contact.FirstName, "Edward")
        self.assertEqual(contact.LastName, "Smith")
        self.assertEqual(contact.Phone, "+44 20 7946 0912")
        self.assertEqual(contact.AccountNumber, "ACC-789")
        self.assertEqual(contact.ContactID, "CON-100")
        self.assertEqual(contact.Address, "123 Industrial Way, London, EC1A 1BB")

    def test_xero_contact_to_monday_item_mapping(self):
        from engine.transformer import xero_contact_to_monday_item
        
        # Test flat and nested address formats and split phone number format
        xero_contact = {
            "ContactID": "CON-12345",
            "Name": "Wayne Enterprises",
            "EmailAddress": "billing@wayne.com",
            "AccountNumber": "WAYNE-01",
            "Phones": [
                {
                    "PhoneType": "DEFAULT",
                    "PhoneCountryCode": "44",
                    "PhoneAreaCode": "7511",
                    "PhoneNumber": "728975"
                }
            ],
            "FirstName": "Bruce",
            "LastName": "Wayne",
            "Addresses": [
                {
                    "AddressType": "STREET",
                    "AddressLine1": "1007 Mountain Drive",
                    "City": "Gotham City",
                    "Country": "USA"
                }
            ]
        }
        
        monday_item = xero_contact_to_monday_item(xero_contact)
        self.assertEqual(monday_item["item_name"], "Wayne Enterprises")
        self.assertEqual(monday_item["column_values"]["contact_id"], "CON-12345")
        self.assertEqual(monday_item["column_values"]["address"], "1007 Mountain Drive, Gotham City, USA")
        self.assertEqual(monday_item["column_values"]["phone"], {"phone": "+447511728975"})
        self.assertEqual(monday_item["column_values"]["first_name"], "Bruce")
        self.assertEqual(monday_item["column_values"]["last_name"], "Wayne")

    def test_xero_contact_to_monday_item_mapping_prioritization(self):
        from engine.transformer import xero_contact_to_monday_item
        
        # Test that the sync prioritises the most complete phone (MOBILE with codes) over a simpler one (DEFAULT)
        xero_contact = {
            "ContactID": "CON-12345",
            "Name": "Wayne Enterprises",
            "EmailAddress": "billing@wayne.com",
            "AccountNumber": "WAYNE-01",
            "Phones": [
                {
                    "PhoneType": "DEFAULT",
                    "PhoneNumber": "728975",
                    "PhoneAreaCode": "",
                    "PhoneCountryCode": ""
                },
                {
                    "PhoneType": "MOBILE",
                    "PhoneCountryCode": "44",
                    "PhoneAreaCode": "7511",
                    "PhoneNumber": "728975"
                }
            ]
        }
        
        monday_item = xero_contact_to_monday_item(xero_contact)
        self.assertEqual(monday_item["column_values"]["phone"], {"phone": "+447511728975"})

    def test_xero_contact_to_monday_item_mapping_text_column(self):
        from engine.transformer import xero_contact_to_monday_item
        
        # Test that the sync returns a plain formatted string when the target column is of type 'text'
        xero_contact = {
            "ContactID": "CON-12345",
            "Name": "Wayne Enterprises",
            "EmailAddress": "billing@wayne.com",
            "AccountNumber": "WAYNE-01",
            "Phones": [
                {
                    "PhoneType": "DEFAULT",
                    "PhoneCountryCode": "44",
                    "PhoneAreaCode": "7511",
                    "PhoneNumber": "728975"
                }
            ]
        }
        
        monday_item = xero_contact_to_monday_item(
            xero_contact,
            column_types={"phone": "text"}
        )
        self.assertEqual(monday_item["column_values"]["phone"], "+44 7511 728975")

    def test_monday_item_to_xero_contact_monday_phone_format(self):
        # Test Monday.com JSON phone formatting parsing
        payload = {
            "transaction_id": "TX-001",
            "company_name": "Acme Holdings",
            "email_address": "billing@acme.com",
            "column_values": [
                {
                    "id": "phone",
                    "title": "Phone",
                    "value": '{"phone":"7511728975","countryShortName":"GB"}'
                }
            ]
        }
        contact = monday_item_to_xero_contact(payload)
        self.assertEqual(contact.Phone, "+447511728975")

    def test_monday_item_to_xero_contact_missing_email(self):
        payload = {
            "transaction_id": "TX-002",
            "company_name": "Invalid Corp",
        }
        with self.assertRaises(TransformerValidationError) as ctx:
            monday_item_to_xero_contact(payload)
        self.assertIn("Missing required Contact Email", str(ctx.exception))

    def test_monday_item_to_xero_product_success(self):
        payload = {
            "id": "PROD-100",
            "item_code": "SKU-CLOUD-01",
            "item_name": "Cloud Advisory Package",
            "unit_price": 2500.0,
            "description": "Enterprise cloud advisory service for 2026",
        }
        product = monday_item_to_xero_product(payload)
        self.assertIsInstance(product, Product)
        self.assertEqual(product.Code, "SKU-CLOUD-01")
        self.assertEqual(product.Name, "Cloud Advisory Package")
        self.assertEqual(product.UnitPrice, 2500.0)
        self.assertEqual(product.Description, "Enterprise cloud advisory service for 2026")

    def test_monday_item_to_xero_product_missing_price(self):
        payload = {
            "item_code": "SKU-001",
            "item_name": "Free Sample",
        }
        with self.assertRaises(TransformerValidationError) as ctx:
            monday_item_to_xero_product(payload)
        self.assertIn("Missing required Product UnitPrice", str(ctx.exception))

    def test_monday_items_to_xero_invoice_with_subitems(self):
        parent_item = {
            "transaction_id": "INV-2026-001",
            "company_name": "Stark Enterprises",
            "email_address": "accounts@stark.com",
            "target_date": "2026-08-01",
            "due_date": "2026-08-31",
        }
        subitems = [
            {
                "item_code": "SKU-ARC-01",
                "item_description": "Arc Reactor Support",
                "quantity": 2,
                "unit_price": 5000.0,
            },
            {
                "item_code": "SKU-AI-02",
                "item_description": "JARVIS Middleware License",
                "quantity": 1,
                "unit_price": 12000.0,
            },
        ]
        invoice = monday_items_to_xero_invoice(parent_item, subitems)
        self.assertIsInstance(invoice, Invoice)
        self.assertEqual(invoice.Contact.Name, "Stark Enterprises")
        self.assertEqual(len(invoice.LineItems), 2)
        self.assertEqual(invoice.TotalAmount, 22000.0)
        self.assertEqual(invoice.IssueDate, "2026-08-01")
        self.assertEqual(invoice.DueDate, "2026-08-31")

    def test_monday_items_to_xero_invoice_with_connect_boards(self):
        # Parent contains relation value referring to Customer
        parent_item = {
            "id": "pulse_parent_123",
            "name": "Invoice #1001",
            "column_values": [
                {
                    "id": "customer_connect",
                    "title": "Customer Link",
                    "linked_items": [{"id": "cust_item_999", "name": "Stark Industries", "column_values": [{"id": "email_col", "text": "billing@stark.com"}]}]
                }
            ]
        }
        
        # Subitem contains relation value referring to Product
        subitems = [
            {
                "id": "subpulse_1",
                "name": "Subitem 1",
                "column_values": [
                    {
                        "id": "quantity_col",
                        "title": "Qty",
                        "value": "2"
                    },
                    {
                        "id": "product_connect",
                        "title": "Product Link",
                        "linked_items": [{"id": "prod_item_888", "name": "Arc Reactor", "column_values": [{"id": "code_col", "text": "ARC-01"}, {"id": "price_col", "text": "500.0"}]}]
                    }
                ]
            }
        ]
        
        # Define mappings
        mappings = {
            "Invoice.Contact.Name": "customer_connect",
            "Invoice.LineItems[0].ItemCode": "product_connect",
            "Invoice.LineItems[0].Quantity": "quantity_col",
        }
        customer_mappings = {
            "Invoice.Contact.Name": "name",
            "Invoice.Contact.EmailAddress": "email_col",
        }
        product_mappings = {
            "Item.Code": "code_col",
            "Item.SalesDetails.UnitPrice": "price_col",
        }
        
        invoice = monday_items_to_xero_invoice(
            parent_item,
            subitems,
            mappings=mappings,
            customer_mappings=customer_mappings,
            product_mappings=product_mappings
        )
        
        self.assertIsInstance(invoice, Invoice)
        self.assertEqual(invoice.Contact.Name, "Stark Industries")
        self.assertEqual(invoice.Contact.Email, "billing@stark.com")
        self.assertEqual(len(invoice.LineItems), 1)
        self.assertEqual(invoice.LineItems[0].ItemCode, "ARC-01")
        self.assertEqual(invoice.LineItems[0].Quantity, 2)
        self.assertEqual(invoice.LineItems[0].UnitAmount, 500.0)


if __name__ == "__main__":
    unittest.main()
