"""
Unit tests for M.I.R.A. Schema Mapping Management Router.
"""

import unittest
from unittest.mock import MagicMock, patch

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.db import Base, User, FieldMapping
    from routes.mappings import ensure_user_mappings, DEFAULT_MAPPING_VERSION, DEFAULT_MAPPINGS, get_column_title, DEFAULT_COLUMN_TITLES
    has_sqlalchemy = True
except ImportError:
    has_sqlalchemy = False


class TestMappingsRouter(unittest.TestCase):
    """Test suite for Schema Mapping database persistence and default seeding."""

    def setUp(self):
        if not has_sqlalchemy:
            self.skipTest("SQLAlchemy module not installed in current Python environment.")
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        if has_sqlalchemy and hasattr(self, "db"):
            self.db.close()
            Base.metadata.drop_all(self.engine)

    def test_ensure_user_mappings_seeding(self):
        """Verifies default mapping rules v2.0.4 are seeded when user has no mappings."""
        mappings = ensure_user_mappings(self.db, user_id=1)

        self.assertEqual(len(mappings), len(DEFAULT_MAPPINGS))
        sources = [m.source_column for m in mappings]
        self.assertIn("transaction_id", sources)
        self.assertIn("company_name", sources)
        self.assertIn("unit_price", sources)
        self.assertEqual(mappings[0].mapping_version, DEFAULT_MAPPING_VERSION)

    def test_mapping_custom_updates(self):
        """Verifies saving custom mapping rules."""
        ensure_user_mappings(self.db, user_id=1)

        # Clear and update
        self.db.query(FieldMapping).filter(FieldMapping.user_id == 1).delete()
        custom_mapping = FieldMapping(
            user_id=1,
            source_column="custom_deal_id",
            target_xero_path="Invoice.Reference",
            custom_override_path="DEAL-{custom_deal_id}",
            mapping_version="v2.0.4",
        )
        self.db.add(custom_mapping)
        self.db.commit()

        updated = self.db.query(FieldMapping).filter(FieldMapping.user_id == 1).all()
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].source_column, "custom_deal_id")
        self.assertEqual(updated[0].custom_override_path, "DEAL-{custom_deal_id}")

    def test_board_mappings_persistence(self):
        """Verifies saving and retrieving board mappings."""
        user_id = 1
        board_id_1 = "board_one"

        # Create mapping rows for Board 1
        fm1 = FieldMapping(
            user_id=user_id,
            board_id=board_id_1,
            target_xero_path="Invoice.Reference",
            source_column="b1_ref",
            custom_override_path="TX-{ref}",
            mapping_version="v2.0.4"
        )

        self.db.add(fm1)
        self.db.commit()

        # Retrieve mappings and check they are stored correctly
        mappings_b1 = self.db.query(FieldMapping).filter(FieldMapping.user_id == user_id, FieldMapping.board_id == board_id_1).all()

        self.assertEqual(len(mappings_b1), 1)
        self.assertEqual(mappings_b1[0].source_column, "b1_ref")

    def test_column_title_helper_and_defaults(self):
        """Verifies get_column_title helper returns friendly column titles."""
        # 1. From DEFAULT_COLUMN_TITLES
        self.assertEqual(get_column_title("transaction_id"), "Transaction ID")
        self.assertEqual(get_column_title("company_name"), "Company Name")
        self.assertEqual(get_column_title("unit_price"), "Unit Price")
        self.assertEqual(get_column_title("cost_price"), "Cost Price")

        # 2. From board_columns list
        mock_board_columns = [
            {"id": "col_custom_1", "title": "Account Number"},
            {"id": "numeric_mm6631e9", "title": "Invoice Total"}
        ]
        self.assertEqual(get_column_title("col_custom_1", mock_board_columns), "Account Number")
        self.assertEqual(get_column_title("numeric_mm6631e9", mock_board_columns), "Invoice Total")

        # 3. Fallback formatting
        self.assertEqual(get_column_title("my_custom_field"), "My Custom Field")
        self.assertEqual(get_column_title(""), "")


if __name__ == "__main__":
    unittest.main()
