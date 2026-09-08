"""
Unit tests for M.I.R.A. System Documentation & Interactive User Guide Router.
"""

import unittest
from starlette.testclient import TestClient
import main
from utils.auth import create_access_token
from models.db import SessionLocal, User

class TestDocumentationPage(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.db = SessionLocal()
        # Ensure a user with role 'user' and role 'admin' exist
        self.super_admin = self.db.query(User).filter(User.role == "super_admin", User.is_active == True).first()
        self.admin = self.db.query(User).filter(User.role == "admin", User.is_active == True).first()
        self.operator = self.db.query(User).filter(User.role == "user", User.is_active == True).first()

    def tearDown(self):
        if hasattr(self, "db") and self.db:
            self.db.close()

    def test_unauthenticated_redirect(self):
        resp = self.client.get("/documentation", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertIn("/login", resp.headers.get("location", ""))

    def _extract_navbar(self, html: str) -> str:
        start_tag = 'border-l border-slate-200 pl-6 text-xs font-semibold'
        start_idx = html.find(start_tag)
        if start_idx == -1:
            return ""
        end_idx = html.find('</div>', start_idx)
        return html[start_idx:end_idx]

    def test_operator_user_role(self):
        self.assertIsNotNone(self.operator, "Active operator user required in test DB")
        token = create_access_token({"user_id": self.operator.id, "email": self.operator.email, "role": "user"})
        self.client.cookies.set("mira_access_token", token)
        resp = self.client.get("/documentation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("System Documentation & Operator Manual", resp.text)
        
        # Verify navbar for operator
        nav = self._extract_navbar(resp.text)
        self.assertIn('href="/sync"', nav)
        self.assertIn('href="/documentation"', nav)
        self.assertNotIn('href="/connectors"', nav)
        self.assertNotIn('href="/mappings"', nav)
        self.assertNotIn('href="/admin/users"', nav)

    def test_admin_role(self):
        self.assertIsNotNone(self.admin, "Active admin user required in test DB")
        token = create_access_token({"user_id": self.admin.id, "email": self.admin.email, "role": "admin"})
        self.client.cookies.set("mira_access_token", token)
        resp = self.client.get("/documentation")
        self.assertEqual(resp.status_code, 200)

        # Verify navbar for admin
        nav = self._extract_navbar(resp.text)
        self.assertIn('href="/sync"', nav)
        self.assertIn('href="/connectors"', nav)
        self.assertIn('href="/mappings"', nav)
        self.assertIn('href="/documentation"', nav)
        self.assertIn('href="/admin/users"', nav)

        # Verify exact ordering in navbar: Mappings -> Documentation -> User Management
        mappings_pos = nav.find('href="/mappings"')
        doc_pos = nav.find('href="/documentation"')
        users_pos = nav.find('href="/admin/users"')
        self.assertTrue(mappings_pos < doc_pos < users_pos, "Documentation must be between Schema Mappings and User Management")

    def test_super_admin_role(self):
        self.assertIsNotNone(self.super_admin, "Active super_admin user required in test DB")
        token = create_access_token({"user_id": self.super_admin.id, "email": self.super_admin.email, "role": "super_admin"})
        self.client.cookies.set("mira_access_token", token)
        resp = self.client.get("/documentation")
        self.assertEqual(resp.status_code, 200)

        # Verify navbar for super admin
        nav = self._extract_navbar(resp.text)
        self.assertIn('href="/sync"', nav)
        self.assertIn('href="/connectors"', nav)
        self.assertIn('href="/mappings"', nav)
        self.assertIn('href="/documentation"', nav)
        self.assertIn('href="/admin/users"', nav)

        mappings_pos = nav.find('href="/mappings"')
        doc_pos = nav.find('href="/documentation"')
        users_pos = nav.find('href="/admin/users"')
        self.assertTrue(mappings_pos < doc_pos < users_pos, "Documentation must be between Schema Mappings and User Management")

    def test_alias_route(self):
        self.assertIsNotNone(self.operator, "Active operator user required in test DB")
        token = create_access_token({"user_id": self.operator.id, "email": self.operator.email, "role": "user"})
        self.client.cookies.set("mira_access_token", token)
        resp = self.client.get("/docs-guide")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("System Documentation & Operator Manual", resp.text)

if __name__ == "__main__":
    unittest.main()
