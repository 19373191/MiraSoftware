"""
Unit tests for data_simulation.py.

Verifies schema layout, uniqueness of transaction IDs, batch lengths, and streaming
generator characteristics.
"""

import unittest
from datetime import datetime
from data_simulation import generate_single_deal, generate_batch, stream_deals
from faker import Faker


class TestDataSimulation(unittest.TestCase):
    """Test suite for the transaction generator simulation script."""

    def setUp(self):
        self.fake = Faker()

    def test_generate_single_deal_schema(self):
        """Verify that a single record contains all expected fields with valid formats."""
        record = generate_single_deal(self.fake)
        
        # Verify required keys exist
        expected_keys = {
            "transaction_id",
            "customer_first_name",
            "customer_last_name",
            "company_name",
            "email_address",
            "item_description",
            "quantity",
            "unit_price",
            "target_date"
        }
        self.assertEqual(set(record.keys()), expected_keys)

        # Check types
        self.setIsInstance(record["transaction_id"], str)
        self.setIsInstance(record["customer_first_name"], str)
        self.setIsInstance(record["customer_last_name"], str)
        self.setIsInstance(record["company_name"], str)
        self.setIsInstance(record["email_address"], str)
        self.setIsInstance(record["item_description"], str)
        self.setIsInstance(record["quantity"], int)
        self.setIsInstance(record["unit_price"], float)
        self.setIsInstance(record["target_date"], str)

        # Verify date string parses correctly
        # Strip trailing Z for datetime parsing
        date_str = record["target_date"].rstrip("Z")
        try:
            datetime.fromisoformat(date_str)
        except ValueError:
            self.fail(f"target_date '{record['target_date']}' is not a valid ISO 8601 format.")

        # Email format basic validation
        self.assertIn("@", record["email_address"])
        self.assertTrue(record["email_address"].endswith(".com"))

    def test_generate_batch_size_and_uniqueness(self):
        """Verify the batch mode returns correct record counts and unique deal IDs."""
        batch_size = 500
        records = generate_batch(batch_size)
        
        self.assertEqual(len(records), batch_size)
        
        # Ensure all transaction IDs are unique
        transaction_ids = {r["transaction_id"] for r in records}
        self.assertEqual(len(transaction_ids), batch_size)

    def test_stream_deals_generator(self):
        """Verify that streaming generator returns the correct number of items sequentially."""
        count = 10
        stream = stream_deals(count=count, delay_seconds=0.0)
        
        records = list(stream)
        self.assertEqual(len(records), count)
        
        # Verify that all elements are dictionary records
        for record in records:
            self.assertIn("transaction_id", record)

    def setIsInstance(self, obj, cls):
        """Helper to assert instance types."""
        self.assertTrue(isinstance(obj, cls), f"Expected instance of {cls}, got {type(obj)}")


if __name__ == "__main__":
    unittest.main()
