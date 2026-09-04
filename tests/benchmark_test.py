"""
M.I.R.A. Automated Benchmarking & Performance Metrics Test Suite.

Generates 500 mock transaction payloads using Faker, executes batch transformation throughput tests,
records latency/accuracy metrics, and exports benchmark_results.json for dissertation reporting.
"""

from datetime import datetime, timezone
import json
import os
import random
import statistics
import time
import unittest
from typing import Any, Dict, List

try:
    from faker import Faker
    fake = Faker("en_GB")
except ImportError:
    fake = None

from engine.transformer import (
    DataTransformer,
    monday_item_to_xero_contact,
    monday_items_to_xero_invoice,
    TransformerValidationError,
)
from utils.logger import logger


def generate_mock_transactions(count: int = 500) -> List[Dict[str, Any]]:
    """
    Programmatically generates mock transaction payloads with customer details,
    catalog items, and invoice quantities/amounts.
    """
    records = []
    companies = [
        "Acme Corp", "Cyberdyne Systems", "Stark Industries", "Wayne Enterprises",
        "Tyrell Corp", "Massive Dynamic", "Initech", "Umbrella Corp", "Globex Corp",
        "Aperture Science", "Oscorp", "Soylent Corp", "Halliwell Ltd", "Vandelay Industries"
    ]
    catalog_items = [
        ("SKU-ADV-01", "Enterprise Advisory Consulting", 1500.0),
        ("SKU-CLOUD-02", "Cloud Middleware Infrastructure License", 2450.0),
        ("SKU-SYNC-03", "Real-Time Automation Connector", 850.0),
        ("SKU-DEV-04", "Custom API Pipeline Development", 3200.0),
        ("SKU-SUPP-05", "24/7 Managed Integration Support", 600.0),
    ]

    random.seed(42)  # Deterministic seed for reproducible dissertation benchmarking

    for i in range(1, count + 1):
        company = random.choice(companies) if not fake else fake.company()
        first_name = f"User{i}" if not fake else fake.first_name()
        last_name = f"Test{i}" if not fake else fake.last_name()
        clean_company_slug = "".join(c for c in company if c.isalnum()).lower()
        clean_first = "".join(c for c in first_name if c.isalnum()).lower()
        clean_last = "".join(c for c in last_name if c.isalnum()).lower()
        email = f"{clean_first}.{clean_last}@{clean_company_slug}.com"
        phone = f"+44 20 7946 {i:04d}"

        sku, item_name, unit_price = random.choice(catalog_items)
        qty = random.randint(1, 10)

        record = {
            "transaction_id": f"TX-{i:05d}",
            "company_name": company,
            "customer_first_name": first_name,
            "customer_last_name": last_name,
            "email_address": email,
            "phone": phone,
            "account_number": f"ACC-{1000 + i}",
            "item_code": sku,
            "item_name": item_name,
            "item_description": f"{item_name} - Q3 Automation Batch",
            "unit_price": unit_price,
            "quantity": qty,
            "target_date": "2026-07-20T12:00:00Z",
        }
        records.append(record)

    logger.info("Generated %d mock transactions for benchmarking.", len(records))
    return records


def run_benchmark_harness(records: List[Dict[str, Any]], batch_size: int = 50) -> Dict[str, Any]:
    """
    Executes benchmark harness over generated records, recording per-record latencies,
    batch throughput, delivery accuracy, and error logs.
    """
    transformer = DataTransformer()
    total_records = len(records)
    individual_latencies_ms: List[float] = []
    failed_transformations: List[Dict[str, Any]] = []
    successful_count = 0

    start_total_time = time.perf_counter()

    # 1. Measure per-record transformation latency
    for rec in records:
        rec_start = time.perf_counter_ns()
        try:
            res = transformer.transform_record_to_invoice(rec)
            rec_end = time.perf_counter_ns()
            latency_ms = (rec_end - rec_start) / 1_000_000.0
            individual_latencies_ms.append(latency_ms)

            if res.get("status") == "success":
                successful_count += 1
            else:
                failed_transformations.append(res)
        except Exception as e:
            rec_end = time.perf_counter_ns()
            latency_ms = (rec_end - rec_start) / 1_000_000.0
            individual_latencies_ms.append(latency_ms)
            failed_transformations.append({
                "record_id": rec.get("transaction_id", "unknown"),
                "error": str(e),
                "original_record": rec,
            })

    end_total_time = time.perf_counter()
    total_execution_time_seconds = end_total_time - start_total_time

    # 2. Compute performance metrics
    median_latency_ms = round(statistics.median(individual_latencies_ms), 4) if individual_latencies_ms else 0.0
    mean_latency_ms = round(statistics.mean(individual_latencies_ms), 4) if individual_latencies_ms else 0.0
    p95_latency_ms = round(statistics.quantiles(individual_latencies_ms, n=20)[18], 4) if len(individual_latencies_ms) >= 20 else mean_latency_ms
    accuracy_percentage = round((successful_count / total_records) * 100.0, 2)
    throughput_rps = round(total_records / total_execution_time_seconds, 2) if total_execution_time_seconds > 0 else 0.0

    # 3. Batch grouping benchmark
    batch_start = time.perf_counter()
    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    batch_responses = []
    for b in batches:
        b_res = transformer.transform_batch_grouped(b)
        batch_responses.append(b_res)
    batch_end = time.perf_counter()
    batch_total_time_seconds = round(batch_end - batch_start, 4)

    results = {
        "system_info": {
            "application": "M.I.R.A. (Middleware for Integration and Real-time Automation)",
            "benchmark_module": "Transformation Engine & Schemas",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_records_processed": total_records,
            "batch_size": batch_size,
            "total_batches_processed": len(batches),
        },
        "performance_metrics": {
            "total_execution_time_seconds": round(total_execution_time_seconds, 4),
            "median_latency_per_record_ms": median_latency_ms,
            "mean_latency_per_record_ms": mean_latency_ms,
            "p95_latency_per_record_ms": p95_latency_ms,
            "throughput_records_per_second": throughput_rps,
            "batch_grouping_execution_seconds": batch_total_time_seconds,
        },
        "accuracy_metrics": {
            "successful_transformations": successful_count,
            "failed_transformations": len(failed_transformations),
            "payload_accuracy_percentage": accuracy_percentage,
            "target_accuracy_threshold_percentage": 99.0,
            "target_accuracy_met": accuracy_percentage >= 99.0,
        },
        "failed_records_log": failed_transformations,
    }

    return results


def save_benchmark_results(results: Dict[str, Any], output_path: str = "benchmark_results.json") -> None:
    """Saves benchmark result metrics to JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
    logger.info("Saved benchmark results to %s", output_path)

    # Also save inside tests/ if outputting to root
    tests_output_path = os.path.join("tests", "benchmark_results.json")
    if output_path != tests_output_path:
        try:
            with open(tests_output_path, "w", encoding="utf-8") as f2:
                json.dump(results, f2, indent=4)
            logger.info("Saved copy of benchmark results to %s", tests_output_path)
        except OSError:
            pass


class TestBenchmarkSuite(unittest.TestCase):
    """Automated benchmark test case verifying delivery accuracy >= 99%."""

    def test_run_500_records_benchmark(self):
        records = generate_mock_transactions(count=500)
        self.assertEqual(len(records), 500)

        results = run_benchmark_harness(records, batch_size=50)
        save_benchmark_results(results, "benchmark_results.json")

        accuracy = results["accuracy_metrics"]["payload_accuracy_percentage"]
        logger.info(
            "Benchmark Results Summary: Execution Time: %.4fs | Median Latency: %.4fms | Accuracy: %.2f%%",
            results["performance_metrics"]["total_execution_time_seconds"],
            results["performance_metrics"]["median_latency_per_record_ms"],
            accuracy,
        )

        self.assertGreaterEqual(
            accuracy,
            99.0,
            f"Payload delivery accuracy target >= 99% not met. Got {accuracy}%",
        )
        self.assertTrue(results["accuracy_metrics"]["target_accuracy_met"])


if __name__ == "__main__":
    records = generate_mock_transactions(count=500)
    res = run_benchmark_harness(records, batch_size=50)
    save_benchmark_results(res, "benchmark_results.json")
    print(json.dumps(res, indent=4))
