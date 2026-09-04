"""
Unit tests for the performance benchmarking script (benchmark.py).

Verifies the mathematical percentile logic (P50/P95), summary statistics calculations,
and CSV/JSON file persistence methods.
"""

import csv
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from benchmark import calculate_percentile, XeroPerformanceBenchmark, save_reports


class TestXeroPerformanceBenchmark(unittest.TestCase):
    """Test suite for benchmark.py functionalities."""

    def test_calculate_percentile_simple(self):
        """Verify percentile calculation on a simple sequential dataset."""
        data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        
        # P50 (median) should be the average of 50 and 60 (since even length)
        # index = (10-1) * 0.5 = 4.5. floor=4 (val=50.0), ceil=5 (val=60.0). P50 = 50 + (60-50)*0.5 = 55.0
        self.assertAlmostEqual(calculate_percentile(data, 50.0), 55.0, places=2)

        # P95 should be at index = (10-1) * 0.95 = 8.55. floor=8 (val=90.0), ceil=9 (val=100.0).
        # P95 = 90 + (100-90)*0.55 = 95.5
        self.assertAlmostEqual(calculate_percentile(data, 95.0), 95.5, places=2)

    def test_calculate_percentile_empty(self):
        """Verify empty list returns 0.0 without errors."""
        self.assertEqual(calculate_percentile([], 95.0), 0.0)

    @patch("benchmark.requests.post")
    def test_send_single_payload_success(self, mock_post):
        """Verify successful single payload sends record, metrics latency, and returns markers."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"status": "success"}
        mock_post.return_value = mock_response

        client = XeroPerformanceBenchmark(target_url="http://mock-url")
        payload = {"transaction_id": "tx-123"}
        
        result = client.send_single_payload(1, payload)

        self.assertEqual(result["request_index"], 1)
        self.assertEqual(result["transaction_id"], "tx-123")
        self.assertEqual(result["status_code"], 201)
        self.assertEqual(result["status_label"], "success")
        self.assertTrue(result["latency_ms"] >= 0.0)

    @patch("benchmark.requests.post")
    def test_send_single_payload_rejection(self, mock_post):
        """Verify validation rejections are captured and cataloged."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"status": "transformation_failed"}
        mock_post.return_value = mock_response

        client = XeroPerformanceBenchmark(target_url="http://mock-url")
        payload = {"transaction_id": "tx-123"}
        
        result = client.send_single_payload(1, payload)

        self.assertEqual(result["status_code"], 422)
        self.assertEqual(result["status_label"], "validation_rejection")

    @patch.object(XeroPerformanceBenchmark, "send_single_payload")
    def test_execute_benchmark_summary_logic(self, mock_send):
        """Verify statistics aggregation compiles correctly from run summaries."""
        # Setup mocks returning distinct latencies: 10, 20, 30, 40, 50ms
        mock_send.side_effect = [
            {"request_index": 0, "transaction_id": "a", "latency_ms": 10.0, "status_code": 201, "status_label": "success"},
            {"request_index": 1, "transaction_id": "b", "latency_ms": 20.0, "status_code": 201, "status_label": "success"},
            {"request_index": 2, "transaction_id": "c", "latency_ms": 30.0, "status_code": 422, "status_label": "validation_rejection"},
            {"request_index": 3, "transaction_id": "d", "latency_ms": 40.0, "status_code": 201, "status_label": "success"},
            {"request_index": 4, "transaction_id": "e", "latency_ms": 50.0, "status_code": 500, "status_label": "failed"}
        ]

        client = XeroPerformanceBenchmark(concurrency=2)
        payloads = [{"id": i} for i in range(5)]
        
        benchmark_results = client.execute_benchmark(payloads)
        summary = benchmark_results["summary"]

        # Assert totals
        self.assertEqual(summary["total_payloads"], 5)
        
        # Assert accuracy counts: 3 success, 1 rejection, 1 failure
        accuracy = summary["delivery_accuracy"]
        self.assertEqual(accuracy["successful_transfers_count"], 3)
        self.assertEqual(accuracy["successful_transfers_percentage"], 60.0)
        self.assertEqual(accuracy["validation_rejections_count"], 1)
        self.assertEqual(accuracy["validation_rejections_percentage"], 20.0)
        self.assertEqual(accuracy["system_failures_count"], 1)
        self.assertEqual(accuracy["system_failures_percentage"], 20.0)

        # Assert Latencies (Note: valid latencies exclude 'failed' label, so data=[10, 20, 30, 40])
        # P50 (median) of [10, 20, 30, 40] is index = 3 * 0.5 = 1.5. floor=20, ceil=30. P50 = 25.0
        # P95 of [10, 20, 30, 40] is index = 3 * 0.95 = 2.85. floor=30, ceil=40. P95 = 30 + 10 * 0.85 = 38.5
        latency = summary["latency_distribution_ms"]
        self.assertEqual(latency["min"], 10.0)
        self.assertEqual(latency["max"], 40.0)
        self.assertEqual(latency["mean"], 25.0)  # (10+20+30+40)/4
        self.assertEqual(latency["median_p50"], 25.0)
        self.assertEqual(latency["p95"], 38.5)

    def test_save_reports_persistence(self):
        """Verify CSV and JSON summaries compile and save successfully on local disk."""
        benchmark_data = {
            "summary": {"total_payloads": 1},
            "details": [
                {
                    "request_index": 0,
                    "transaction_id": "tx-1",
                    "status_code": 201,
                    "status_label": "success",
                    "latency_ms": 15.0
                }
            ]
        }

        # Save to temp paths
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "report.json")
            csv_path = os.path.join(temp_dir, "report.csv")
            
            save_reports(benchmark_data, json_path=json_path, csv_path=csv_path)
            
            # Assert file exists
            self.assertTrue(os.path.exists(json_path))
            self.assertTrue(os.path.exists(csv_path))

            # Verify JSON content
            with open(json_path, "r", encoding="utf-8") as f:
                loaded_json = json.load(f)
            self.assertEqual(loaded_json["summary"]["total_payloads"], 1)

            # Verify CSV content
            with open(csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 2)  # Header + 1 data row
            self.assertEqual(rows[0][0], "request_index")
            self.assertEqual(rows[1][0], "0")
            self.assertEqual(rows[1][4], "15.0")


if __name__ == "__main__":
    unittest.main()
