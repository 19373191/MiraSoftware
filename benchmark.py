"""
Testing and Performance Benchmarking Engine.

This script executes performance load-testing on the M.I.R.A. integration webhook
server. It injects simulated B2B transaction payloads, measures round-trip latency
in milliseconds, tracks payload delivery accuracy (successes vs. validation rejections),
and exports the computed metrics in JSON and CSV formats for dissertation visualisations.

All naming conventions, comments, and logging conform to UK English.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

import requests

# Default endpoint configuration
DEFAULT_TARGET_URL = "http://localhost:5000/webhook/monday"
DEFAULT_WEBHOOK_TOKEN = "mira_secure_token_2026"


def calculate_percentile(data: List[float], percentile: float) -> float:
    """
    Calculate the percentile value of a list of numeric values using linear interpolation.

    Args:
        data: List of numeric values (latencies).
        percentile: The target percentile (e.g. 50.0, 95.0).

    Returns:
        The calculated percentile value.
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    index = (len(sorted_data) - 1) * (percentile / 100.0)
    floor_idx = int(index)
    ceil_idx = floor_idx + 1
    if ceil_idx < len(sorted_data):
        # Linear interpolation between closest ranks
        return sorted_data[floor_idx] + (sorted_data[ceil_idx] - sorted_data[floor_idx]) * (index - floor_idx)
    return sorted_data[floor_idx]


class XeroPerformanceBenchmark:
    """
    Performance benchmarking framework measuring webhook synchronization speed
    and payload delivery accuracy.
    """

    def __init__(
        self,
        target_url: str = DEFAULT_TARGET_URL,
        auth_token: str = DEFAULT_WEBHOOK_TOKEN,
        concurrency: int = 1
    ):
        """
        Initialise the benchmarking client.

        Args:
            target_url: Endpoint address of the Monday webhook server.
            auth_token: Authorisation Bearer token for server security.
            concurrency: Number of parallel request threads.
        """
        self.target_url = target_url
        self.auth_token = auth_token
        self.concurrency = concurrency
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth_token}"
        }

    def send_single_payload(self, request_index: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a single payload and measure its latency.

        Args:
            request_index: Index number of the request.
            payload: JSON transaction dictionary.

        Returns:
            A dictionary containing latency, status code, and success markers.
        """
        transaction_id = payload.get("transaction_id", "unknown")
        start_time = time.perf_counter()
        
        try:
            response = requests.post(
                self.target_url,
                json=payload,
                headers=self.headers,
                timeout=30
            )
            status_code = response.status_code
            response_json = response.json() if status_code in [200, 201, 422] else {}
        except requests.exceptions.RequestException as e:
            status_code = 0
            response_json = {"error": str(e)}

        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000.0

        # Classify the outcome
        status_label = "failed"
        if status_code == 201:
            status_label = "success"
        elif status_code == 422:
            status_label = "validation_rejection"

        return {
            "request_index": request_index,
            "transaction_id": transaction_id,
            "latency_ms": round(latency_ms, 2),
            "status_code": status_code,
            "status_label": status_label,
            "response": response_json
        }

    def execute_benchmark(self, payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute the performance test suite on the provided list of payloads.

        Args:
            payloads: List of transaction payloads.

        Returns:
            A dictionary of summary statistics and raw results.
        """
        total_payloads = len(payloads)
        results: List[Dict[str, Any]] = [None] * total_payloads  # type: ignore

        print(
            f"Starting performance benchmark. Injecting {total_payloads} records "
            f"with concurrency={self.concurrency}...",
            file=sys.stderr
        )

        start_benchmark_time = time.perf_counter()

        # Execute requests concurrently using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_index = {
                executor.submit(self.send_single_payload, idx, payload): idx
                for idx, payload in enumerate(payloads)
            }
            
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    res = future.result()
                    results[index] = res
                    # Print progress indicators
                    if (index + 1) % 50 == 0 or (index + 1) == total_payloads:
                        print(f"Processed {index + 1}/{total_payloads} requests...", file=sys.stderr)
                except Exception as e:
                    results[index] = {
                        "request_index": index,
                        "transaction_id": payloads[index].get("transaction_id", "unknown"),
                        "latency_ms": 0.0,
                        "status_code": 0,
                        "status_label": "failed",
                        "response": {"error": str(e)}
                    }

        end_benchmark_time = time.perf_counter()
        total_time_seconds = end_benchmark_time - start_benchmark_time

        # Extract latencies for successful and validation_rejection records
        valid_latencies = [
            r["latency_ms"] for r in results if r["status_label"] in ["success", "validation_rejection"]
        ]

        # Calculate Accuracy metrics
        success_count = sum(1 for r in results if r["status_label"] == "success")
        rejection_count = sum(1 for r in results if r["status_label"] == "validation_rejection")
        failure_count = sum(1 for r in results if r["status_label"] == "failed")

        success_percentage = (success_count / total_payloads) * 100.0
        rejection_percentage = (rejection_count / total_payloads) * 100.0
        failure_percentage = (failure_count / total_payloads) * 100.0

        # Calculate Latency distributions
        min_latency = min(valid_latencies) if valid_latencies else 0.0
        max_latency = max(valid_latencies) if valid_latencies else 0.0
        mean_latency = (sum(valid_latencies) / len(valid_latencies)) if valid_latencies else 0.0
        median_latency = calculate_percentile(valid_latencies, 50.0)
        p95_latency = calculate_percentile(valid_latencies, 95.0)

        summary = {
            "benchmark_execution_date": datetime.now(timezone.utc).isoformat() + "Z",
            "total_payloads": total_payloads,
            "concurrency": self.concurrency,
            "total_duration_seconds": round(total_time_seconds, 2),
            "throughput_requests_per_second": round(total_payloads / total_time_seconds, 2),
            "delivery_accuracy": {
                "successful_transfers_count": success_count,
                "successful_transfers_percentage": round(success_percentage, 2),
                "validation_rejections_count": rejection_count,
                "validation_rejections_percentage": round(rejection_percentage, 2),
                "system_failures_count": failure_count,
                "system_failures_percentage": round(failure_percentage, 2)
            },
            "latency_distribution_ms": {
                "min": round(min_latency, 2),
                "max": round(max_latency, 2),
                "mean": round(mean_latency, 2),
                "median_p50": round(median_latency, 2),
                "p95": round(p95_latency, 2)
            }
        }

        return {
            "summary": summary,
            "details": results
        }


def save_reports(
    benchmark_data: Dict[str, Any],
    json_path: str = "benchmark_report.json",
    csv_path: str = "benchmark_report.csv"
) -> None:
    """
    Save the benchmarking statistics to local CSV and JSON files for MSc plotting.

    Args:
        benchmark_data: Mapped results dictionary.
        json_path: Output JSON path.
        csv_path: Output CSV path.
    """
    # Write JSON Report
    try:
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(benchmark_data, json_file, indent=4)
        print(f"JSON summary report successfully saved to: {json_path}", file=sys.stderr)
    except OSError as e:
        print(f"Error saving JSON report: {e}", file=sys.stderr)

    # Write CSV Report (records row-by-row details for matplotlib visualisations)
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            # Headers
            writer.writerow([
                "request_index",
                "transaction_id",
                "status_code",
                "status_label",
                "latency_ms"
            ])
            for detail in benchmark_data["details"]:
                writer.writerow([
                    detail["request_index"],
                    detail["transaction_id"],
                    detail["status_code"],
                    detail["status_label"],
                    detail["latency_ms"]
                ])
        print(f"CSV data records successfully saved to: {csv_path}", file=sys.stderr)
    except OSError as e:
        print(f"Error saving CSV report: {e}", file=sys.stderr)


def print_ascii_summary(summary: Dict[str, Any]) -> None:
    """Print a clean summary report in stdout."""
    accuracy = summary["delivery_accuracy"]
    latency = summary["latency_distribution_ms"]

    print("\n" + "=" * 55)
    print("      PROJECT M.I.R.A. PERFORMANCE BENCHMARK REPORT      ")
    print("=" * 55)
    print(f"Execution Date:   {summary['benchmark_execution_date']}")
    print(f"Total Payloads:   {summary['total_payloads']}")
    print(f"Concurrency:      {summary['concurrency']}")
    print(f"Total Duration:   {summary['total_duration_seconds']} seconds")
    print(f"Throughput:       {summary['throughput_requests_per_second']} requests/sec")
    
    print("-" * 55)
    print(" PAYLOAD DELIVERY ACCURACY")
    print("-" * 55)
    print(f" Successful Transfers:  {accuracy['successful_transfers_count']} ({accuracy['successful_transfers_percentage']}%)")
    print(f" Validation Rejections: {accuracy['validation_rejections_count']} ({accuracy['validation_rejections_percentage']}%)")
    print(f" System Failures:       {accuracy['system_failures_count']} ({accuracy['system_failures_percentage']}%)")
    
    print("-" * 55)
    print(" SYSTEM LATENCY DISTRIBUTION (ms)")
    print("-" * 55)
    print(f" Min Latency:     {latency['min']} ms")
    print(f" Max Latency:     {latency['max']} ms")
    print(f" Mean Latency:    {latency['mean']} ms")
    print(f" Median (P50):    {latency['median_p50']} ms")
    print(f" P95 Latency:     {latency['p95']} ms")
    print("=" * 55 + "\n")


def main():
    """CLI execution entrypoint."""
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        description="Inject payloads and compile latency / accuracy performance benchmarks."
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_TARGET_URL,
        help=f"Target URL of monday webhook (default: {DEFAULT_TARGET_URL})"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=DEFAULT_WEBHOOK_TOKEN,
        help="Authorization token for Monday webhook verification"
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default="transactions_simulation.json",
        help="JSON file containing mock transactions (default: transactions_simulation.json)"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Limit number of transactions sent during the benchmark (defaults to all)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent worker threads (default: 5)"
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default="benchmark_report.json",
        help="Path to export the summary JSON (default: benchmark_report.json)"
    )
    parser.add_argument(
        "--csv-output",
        type=str,
        default="benchmark_report.csv",
        help="Path to export raw records to CSV (default: benchmark_report.csv)"
    )

    args = parser.parse_args()

    # Load mock transaction payloads
    if not os.path.exists(args.input_file):
        print(
            f"Error: Input transactions file not found at: {args.input_file}. "
            "Please run 'data_simulation.py --mode file' first to generate payload pool.",
            file=sys.stderr
        )
        sys.exit(1)

    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            payloads = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    # Slice payload list if count argument is set
    if args.count:
        payloads = payloads[:args.count]

    # Pre-ping target URL to check server connection
    try:
        health_url = args.url.replace("/webhook/monday", "/health")
        requests.get(health_url, timeout=5)
    except requests.exceptions.RequestException:
        print(
            f"Error: Could not connect to M.I.R.A. health check server at {health_url}.\n"
            "Please ensure the Flask application is running before executing the benchmark.",
            file=sys.stderr
        )
        sys.exit(1)

    # Execute performance run
    benchmark_client = XeroPerformanceBenchmark(
        target_url=args.url,
        auth_token=args.token,
        concurrency=args.concurrency
    )

    benchmark_data = benchmark_client.execute_benchmark(payloads)
    
    # Save files
    save_reports(
        benchmark_data,
        json_path=args.json_output,
        csv_path=args.csv_output
    )

    # Print summary to console
    print_ascii_summary(benchmark_data["summary"])


if __name__ == "__main__":
    main()
