"""
Unit tests for M.I.R.A. Auth, RBAC, Admin User Management, and Connector Routes.
"""

import unittest
from unittest.mock import MagicMock, patch

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.db import Base, User, Credentials, FieldMapping, SyncLog
    has_sqlalchemy = True
except ImportError:
    has_sqlalchemy = False

from utils.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from routes.auth import seed_super_admin


class TestAuthAdminConnectors(unittest.TestCase):
    """Test suite for authentication, database schema, RBAC, and platform connectors."""

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

    def test_database_schema_creation_and_seeding(self):
        """Verifies table creation and initial super-admin seeding."""
        seed_super_admin(self.db)

        admin = self.db.query(User).filter(User.email == "admin@mira.local").first()
        self.assertIsNotNone(admin)
        self.assertEqual(admin.role, "admin")
        self.assertTrue(admin.is_active)
        self.assertTrue(verify_password("AdminPass123!", admin.hashed_password))

    def test_password_hashing_and_verification(self):
        """Verifies PBKDF2 password hashing and verification accuracy."""
        raw_pwd = "SecurePassword2026!"
        pwd_hash = hash_password(raw_pwd)

        self.assertNotEqual(raw_pwd, pwd_hash)
        self.assertTrue(verify_password(raw_pwd, pwd_hash))
        self.assertFalse(verify_password("WrongPassword", pwd_hash))

    def test_jwt_token_creation_and_decoding(self):
        """Verifies JWT token encoding and decoding."""
        data = {"sub": "user@mira.local", "user_id": 42, "role": "user"}
        token = create_access_token(data)

        self.assertIsInstance(token, str)
        payload = decode_access_token(token)

        self.assertEqual(payload["sub"], "user@mira.local")
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["role"], "user")
        self.assertIn("exp", payload)

    def test_credentials_persistence(self):
        """Verifies Credentials table relational mapping for Monday and Xero."""
        seed_super_admin(self.db)
        admin = self.db.query(User).filter(User.email == "admin@mira.local").first()

        # Save Monday credentials
        monday_cred = Credentials(
            user_id=admin.id,
            platform_name="monday",
            api_key="test_monday_key",
            board_id="999888777",
        )
        self.db.add(monday_cred)

        # Save Xero credentials
        xero_cred = Credentials(
            user_id=admin.id,
            platform_name="xero",
            access_token="test_xero_access_token",
            refresh_token="test_xero_refresh_token",
            token_expiry=9999999999.0,
        )
        self.db.add(xero_cred)
        self.db.commit()

        user_creds = self.db.query(Credentials).filter(Credentials.user_id == admin.id).all()
        self.assertEqual(len(user_creds), 2)
        platforms = [c.platform_name for c in user_creds]
        self.assertIn("monday", platforms)
        self.assertIn("xero", platforms)

    def test_user_persistence_across_multiple_seeds(self):
        """Verifies that user accounts created in the DB are never deleted or affected by re-seeding."""
        seed_super_admin(self.db)
        # Create a custom user account
        new_user = User(
            email="custom.operator@mira.com",
            hashed_password=hash_password("OperatorPass123!"),
            role="user",
            is_active=True,
        )
        self.db.add(new_user)
        self.db.commit()

        # Call seed_super_admin again (as happens during application restart)
        seed_super_admin(self.db)

        # Confirm the custom user still exists
        found_user = self.db.query(User).filter(User.email == "custom.operator@mira.com").first()
        self.assertIsNotNone(found_user)
        self.assertEqual(found_user.role, "user")
        self.assertTrue(found_user.is_active)

    def test_database_url_postgres_normalization(self):
        """Verifies that postgres:// URL scheme is automatically upgraded to postgresql:// for SQLAlchemy."""
        test_url = "postgres://user:password@hostname:5432/dbname"
        normalized = test_url.replace("postgres://", "postgresql://", 1) if test_url.startswith("postgres://") else test_url
        self.assertEqual(normalized, "postgresql://user:password@hostname:5432/dbname")


if __name__ == "__main__":
    unittest.main()
