"""
Unit tests for M.I.R.A. Synchronization Control Panel & Sync Engine Router.
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from routes.sync import generate_mock_payloads

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.db import Base, SyncLog
    has_sqlalchemy = True
except ImportError:
    has_sqlalchemy = False


class TestSyncRouter(unittest.TestCase):
    """Test suite for batch sync payload generation and SyncLog persistence."""

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

    def test_mock_payload_generation(self):
        """Verifies synthetic payload generation for sync benchmarking."""
        payloads = generate_mock_payloads(500)
        self.assertEqual(len(payloads), 500)
        self.assertIn("transaction_id", payloads[0])
        self.assertIn("company_name", payloads[0])
        self.assertIn("unit_price", payloads[0])

    def test_sync_log_persistence(self):
        """Verifies database recording of SyncLog audit entries."""
        log_entry = SyncLog(
            status="SUCCESS",
            payload_count=500,
            error_details="Processed 500 records in 0.05s",
        )
        self.db.add(log_entry)
        self.db.commit()

        logs = self.db.query(SyncLog).all()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].payload_count, 500)
        self.assertEqual(logs[0].status, "SUCCESS")

    def test_parse_xero_date(self):
        """Verifies parse_xero_date parses both Microsoft and ISO formats."""
        from routes.sync import parse_xero_date
        from datetime import datetime, timezone
        
        # Test ISO format
        dt1 = parse_xero_date("2026-08-13T22:05:14Z")
        self.assertEqual(dt1, datetime(2026, 8, 13, 22, 5, 14, tzinfo=timezone.utc))
        
        # Test Microsoft JSON date format
        dt2 = parse_xero_date("/Date(1785798236000+0000)/")
        self.assertEqual(dt2, datetime(2026, 8, 3, 23, 3, 56, tzinfo=timezone.utc))
        
        # Test invalid date
        self.assertIsNone(parse_xero_date("invalid-date"))

    def test_mock_payloads_have_timestamps(self):
        """Verifies generate_mock_payloads adds updated_at field."""
        payloads = generate_mock_payloads(10)
        self.assertEqual(len(payloads), 10)
        self.assertIn("updated_at", payloads[0])

    @patch("routes.sync.get_user_credentials")
    def test_run_spa_sync_in_background_filtering(self, mock_get_creds):
        """Verifies filtering of simulated contacts/payloads in run_spa_sync_in_background."""
        # 1. Setup mock credentials
        mock_get_creds.return_value = MagicMock(access_token="mock_token", board_id="default")
        
        # 2. Add an older successful SyncLog in the SQLite db
        from datetime import datetime, timezone, timedelta
        from routes.sync import run_spa_sync_in_background, SPA_LOGS
        
        now = datetime.now(timezone.utc)
        
        # Scenario: xero_to_monday filtering
        # Last successful sync was 2.5 hours ago to avoid timing jitter
        last_run_time = now - timedelta(hours=2, minutes=30)
        old_log = SyncLog(
            timestamp=last_run_time,
            status="SUCCESS",
            direction="xero_to_monday",
            payload_count=5,
            error_details="Previous run details"
        )
        self.db.add(old_log)
        self.db.commit()
        
        # Mock SessionLocal to return self.db
        with patch("models.db.SessionLocal", return_value=self.db), \
             patch.dict("os.environ", {"XERO_DRY_RUN": "True"}):
            
            run_spa_sync_in_background(
                direction="xero_to_monday",
                board_id_1="default",
                board_id_3="5101138235",
                batch_count=100,
                group_by_company=True,
                target_account="200",
                user_id=1
            )
            
            # The mock contacts have updated times:
            # now - 5 hours, now - 4 hours, now - 3 hours (all <= cutoff_time) -> filtered out!
            # now - 2 hours, now - 1 hour (both > cutoff_time) -> should be synced!
            # So only 2 contacts should be synced!
            logs_text = [log["message"] for log in SPA_LOGS]
            
            retrieved_log = next(msg for msg in logs_text if "Retrieved" in msg)
            self.assertIn("2 contacts", retrieved_log)
            
            # Verify new SyncLog was persisted
            new_logs = self.db.query(SyncLog).filter(SyncLog.direction == "xero_to_monday").order_by(SyncLog.timestamp.desc()).all()
            self.assertEqual(len(new_logs), 2)  # the old one + the new one
            self.assertEqual(new_logs[0].payload_count, 3)
            self.assertEqual(new_logs[0].direction, "xero_to_monday")

    @patch("routes.sync.get_user_credentials")
    def test_run_spa_sync_with_custom_since_date(self, mock_get_creds):
        """Verifies that providing a custom since_date filters records created on or after that date."""
        mock_get_creds.return_value = MagicMock(access_token="mock_token", board_id="default")
        from routes.sync import run_spa_sync_in_background, SPA_LOGS
        
        with patch("models.db.SessionLocal", return_value=self.db), \
             patch.dict("os.environ", {"XERO_DRY_RUN": "True"}):
            
            # Use past date: 2020-01-01 -> all simulated records are after 2020-01-01
            run_spa_sync_in_background(
                direction="xero_to_monday",
                board_id_1="default",
                board_id_3="5101138235",
                batch_count=100,
                group_by_company=True,
                target_account="200",
                user_id=1,
                since_date="2020-01-01"
            )
            
            logs_text = [log["message"] for log in SPA_LOGS]
            self.assertTrue(any("User Specified Date Filter" in msg for msg in logs_text))
            
            retrieved_log = next(msg for msg in logs_text if "Retrieved" in msg and "contacts" in msg)
            self.assertIn("5 contacts", retrieved_log)

    @patch("routes.sync.get_user_credentials")
    def test_run_spa_sync_with_future_since_date(self, mock_get_creds):
        """Verifies that a future since_date filters out all records."""
        mock_get_creds.return_value = MagicMock(access_token="mock_token", board_id="default")
        from routes.sync import run_spa_sync_in_background, SPA_LOGS
        
        with patch("models.db.SessionLocal", return_value=self.db), \
             patch.dict("os.environ", {"XERO_DRY_RUN": "True"}):
            
            # Future date: 2099-01-01 -> no simulated records should match
            run_spa_sync_in_background(
                direction="xero_to_monday",
                board_id_1="default",
                board_id_3="5101138235",
                batch_count=100,
                group_by_company=True,
                target_account="200",
                user_id=1,
                since_date="2099-01-01"
            )
            
            logs_text = [log["message"] for log in SPA_LOGS]
            retrieved_log = next(msg for msg in logs_text if "Retrieved" in msg and "contacts" in msg)
            self.assertIn("0 contacts", retrieved_log)

    @patch("requests.get")
    def test_xero_connector_if_modified_since_header(self, mock_get):
        """Verifies XeroConnector adds If-Modified-Since header when if_modified_since is passed."""
        from connectors.xero_connector import XeroConnector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"Invoices": [{"InvoiceNumber": "INV-0054"}]}
        mock_get.return_value = mock_resp

        mock_oauth = MagicMock()
        mock_oauth.get_bearer_header.return_value = {"Authorization": "Bearer mock", "Xero-tenant-id": "mock-tenant"}

        conn = XeroConnector(tenant_id="mock-tenant", oauth_handler=mock_oauth)
        cutoff = datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)
        invs = conn.get_invoices(if_modified_since=cutoff, page=1)

        self.assertEqual(len(invs), 1)
        self.assertEqual(invs[0]["InvoiceNumber"], "INV-0054")
        mock_get.assert_called_once()
        call_headers = mock_get.call_args[1]["headers"]
        self.assertIn("If-Modified-Since", call_headers)
        self.assertEqual(call_headers["If-Modified-Since"], "Mon, 14 Sep 2026 00:00:00 GMT")


if __name__ == "__main__":
    unittest.main()

