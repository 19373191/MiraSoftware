"""
Unit tests for XeroTransactionTransformer.

Verifies the Active Data Validation Layer, single invoice structure transformation,
error isolation, and B2B multi-line grouping.
"""

import unittest
from transformer import XeroTransactionTransformer


class TestXeroTransactionTransformer(unittest.TestCase):
    """Test suite for the middleware transformation engine."""

    def setUp(self):
        self.transformer = XeroTransactionTransformer(default_account_code="200")
        
        # Valid B2B monday.com simulated record
        self.valid_record = {
            "transaction_id": "abc123xyz-456",
            "customer_first_name": "Edward",
            "customer_last_name": "Smith",
            "company_name": "Innovate Labs Ltd",
            "email_address": "edward.smith@innovatelabs.co.uk",
            "item_description": "Enterprise SaaS Subscription (Annual)",
            "quantity": 3,
            "unit_price": 4500.00,
            "target_date": "2026-07-15T09:00:00Z"
        }

    def test_validation_valid_record(self):
        """Verify valid records pass validation with no errors."""
        errors = self.transformer.validate_record(self.valid_record)
        self.assertEqual(errors, [])

    def test_validation_missing_fields(self):
        """Verify errors are captured when required fields are missing."""
        invalid_record = self.valid_record.copy()
        del invalid_record["company_name"]
        del invalid_record["quantity"]

        errors = self.transformer.validate_record(invalid_record)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("company_name" in err for err in errors))
        self.assertTrue(any("quantity" in err for err in errors))

    def test_validation_empty_fields(self):
        """Verify errors are captured when fields are empty string."""
        invalid_record = self.valid_record.copy()
        invalid_record["customer_first_name"] = "  "

        errors = self.transformer.validate_record(invalid_record)
        self.assertEqual(len(errors), 1)
        self.assertIn("customer_first_name", errors[0])

    def test_validation_invalid_email(self):
        """Verify invalid email syntax triggers a validation error."""
        invalid_record = self.valid_record.copy()
        invalid_record["email_address"] = "edward.smith_at_innovatelabs.com"

        errors = self.transformer.validate_record(invalid_record)
        self.assertEqual(len(errors), 1)
        self.assertIn("email_address", errors[0])

    def test_validation_invalid_numeric_values(self):
        """Verify quantity and unit_price are checked for positive ranges."""
        invalid_record = self.valid_record.copy()
        invalid_record["quantity"] = 0
        invalid_record["unit_price"] = -150.0

        errors = self.transformer.validate_record(invalid_record)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("quantity" in err.lower() for err in errors))
        self.assertTrue(any("unit price" in err.lower() for err in errors))

    def test_validation_invalid_date(self):
        """Verify invalid date format raises validation error."""
        invalid_record = self.valid_record.copy()
        invalid_record["target_date"] = "15th July 2026"

        errors = self.transformer.validate_record(invalid_record)
        self.assertEqual(len(errors), 1)
        self.assertIn("target_date", errors[0])

    def test_transform_single_success(self):
        """Verify successful single record mapping format."""
        result = self.transformer.transform_single(self.valid_record, status="SUBMITTED")
        
        self.assertEqual(result["status"], "success")
        invoice = result["invoice"]
        
        self.assertEqual(invoice["Type"], "ACCREC")
        self.assertEqual(invoice["Status"], "SUBMITTED")
        self.assertEqual(invoice["Contact"]["Name"], "Innovate Labs Ltd")
        self.assertEqual(invoice["Contact"]["EmailAddress"], "edward.smith@innovatelabs.co.uk")
        self.assertEqual(invoice["Date"], "2026-07-15")
        self.assertEqual(invoice["DueDate"], "2026-08-14")
        self.assertEqual(invoice["Reference"], "TX-abc123xy")
        self.assertEqual(len(invoice["LineItems"]), 1)
        self.assertEqual(invoice["LineItems"][0]["Description"], "Enterprise SaaS Subscription (Annual)")
        self.assertEqual(invoice["LineItems"][0]["UnitAmount"], 4500.00)
        self.assertEqual(invoice["LineItems"][0]["AccountCode"], "200")

    def test_transform_single_failure_no_crash(self):
        """Verify invalid record does not crash, but returns structured validation failure error payload."""
        invalid_record = self.valid_record.copy()
        invalid_record["quantity"] = -10
        
        result = self.transformer.transform_single(invalid_record)
        
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["record_id"], "abc123xyz-456")
        self.assertEqual(len(result["validation_errors"]), 1)
        self.assertIn("quantity", result["validation_errors"][0].lower())
        self.assertEqual(result["original_record"], invalid_record)

    def test_transform_batch_grouped_multi_line(self):
        """Verify grouping maps multiple transaction items to a single multi-line invoice by company."""
        tx1 = self.valid_record.copy()
        tx1["transaction_id"] = "tx-1-id"
        tx1["item_description"] = "SaaS Licence"
        tx1["target_date"] = "2026-07-10T12:00:00Z"
        
        tx2 = self.valid_record.copy()
        tx2["transaction_id"] = "tx-2-id"
        tx2["item_description"] = "Consulting Hour"
        tx2["quantity"] = 10
        tx2["unit_price"] = 150.00
        tx2["target_date"] = "2026-07-12T15:00:00Z"

        # Invalid record to test isolation
        tx_invalid = self.valid_record.copy()
        tx_invalid["transaction_id"] = "tx-invalid-id"
        tx_invalid["company_name"] = ""

        records = [tx1, tx2, tx_invalid]
        
        invoices, errors = self.transformer.transform_batch_grouped(records, status="DRAFT")
        
        # Assertions for successfully grouped invoice
        self.assertEqual(len(invoices), 1)
        invoice = invoices[0]
        self.assertEqual(invoice["Contact"]["Name"], "Innovate Labs Ltd")
        
        # Verify Date uses the earliest transaction date (July 10th instead of July 12th)
        self.assertEqual(invoice["Date"], "2026-07-10")
        self.assertEqual(invoice["DueDate"], "2026-08-09")
        
        # Verify multi-line line items
        self.assertEqual(len(invoice["LineItems"]), 2)
        self.assertEqual(invoice["LineItems"][0]["Description"], "SaaS Licence")
        self.assertEqual(invoice["LineItems"][1]["Description"], "Consulting Hour")
        self.assertEqual(invoice["LineItems"][1]["Quantity"], 10)
        self.assertEqual(invoice["LineItems"][1]["UnitAmount"], 150.00)

        # Assertions for failed records isolation
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["record_id"], "tx-invalid-id")
        self.assertEqual(errors[0]["status"], "validation_failed")


if __name__ == "__main__":
    unittest.main()
