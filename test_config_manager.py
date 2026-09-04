"""
Unit tests for the mapping configuration manager (config_manager.py).

Verifies fallback behavior, defaults parsing, auto-recovery of keys, and strict
empty-string validation checks.
"""

import json
import os
import tempfile
import unittest

from config_manager import load_mapping, save_mapping, DEFAULT_MAPPINGS, MANDATORY_KEYS


class TestConfigManager(unittest.TestCase):
    """Test suite asserting configuration mapping validation rules."""

    def setUp(self):
        # Create temp folder to isolate file creations
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, "test_config.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_mapping_fallback_to_defaults(self):
        """Verify load_mapping writes and returns defaults when file is missing."""
        self.assertFalse(os.path.exists(self.config_path))
        
        mappings = load_mapping(self.config_path)
        
        self.assertTrue(os.path.exists(self.config_path))
        self.assertEqual(mappings, DEFAULT_MAPPINGS)

    def test_load_mapping_success(self):
        """Verify load_mapping successfully reads existing mapping configuration."""
        custom_mapping = {"transaction_id": "Ref", "company_name": "Org"}
        # Fill missing mandatory keys with defaults for success loading
        for key in MANDATORY_KEYS:
            if key not in custom_mapping:
                custom_mapping[key] = DEFAULT_MAPPINGS[key]
                
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(custom_mapping, f)

        loaded_mappings = load_mapping(self.config_path)
        self.assertEqual(loaded_mappings["transaction_id"], "Ref")
        self.assertEqual(loaded_mappings["company_name"], "Org")

    def test_load_mapping_recovers_missing_mandatory_keys(self):
        """Verify load_mapping auto-heals config files missing mandatory fields."""
        incomplete_mapping = {
            "transaction_id": "Custom.Ref"
            # Missing company_name, email_address, etc.
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(incomplete_mapping, f)

        loaded = load_mapping(self.config_path)
        # Check company_name was healed back to default
        self.assertEqual(loaded["transaction_id"], "Custom.Ref")
        self.assertEqual(loaded["company_name"], DEFAULT_MAPPINGS["company_name"])

    def test_save_mapping_validation_missing_key(self):
        """Verify save_mapping rejects payloads missing mandatory parameters."""
        incomplete_payload = {
            "transaction_id": "Invoice.Reference"
            # Missing other mandatory keys
        }
        success, errors = save_mapping(incomplete_payload, self.config_path)
        
        self.assertFalse(success)
        self.assertTrue(any("company_name" in err for err in errors))
        self.assertFalse(os.path.exists(self.config_path))

    def test_save_mapping_validation_blank_value(self):
        """Verify save_mapping rejects blank or whitespace string mappings."""
        blank_payload = DEFAULT_MAPPINGS.copy()
        blank_payload["company_name"] = "   "  # Whitespace blank
        
        success, errors = save_mapping(blank_payload, self.config_path)
        
        self.assertFalse(success)
        self.assertTrue(any("company_name" in err for err in errors))
        self.assertFalse(os.path.exists(self.config_path))

    def test_save_mapping_success(self):
        """Verify save_mapping persists valid mappings successfully to disk."""
        valid_payload = DEFAULT_MAPPINGS.copy()
        valid_payload["transaction_id"] = "Invoice.RefOverride"
        
        success, errors = save_mapping(valid_payload, self.config_path)
        
        self.assertTrue(success)
        self.assertEqual(len(errors), 0)
        self.assertTrue(os.path.exists(self.config_path))

        # Assert data was actually written
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data["transaction_id"], "Invoice.RefOverride")


if __name__ == "__main__":
    unittest.main()
