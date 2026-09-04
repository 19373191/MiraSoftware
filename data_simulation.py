"""
Data Simulation Script for B2B Sales Transactions.

This script uses the 'faker' library to programmatically generate realistic B2B
closed-won sales transaction records. It supports batch output (as a single JSON array)
and streaming output (simulating real-time webhook payloads from tools like Monday.com).

Fields generated:
- transaction_id (UUID4)
- customer_first_name
- customer_last_name
- company_name
- email_address
- item_description
- quantity
- unit_price
- target_date (ISO 8601 timestamp)
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, List

from faker import Faker

# Predefined B2B sales items for realistic item descriptions
B2B_SALES_ITEMS = [
    "Enterprise SaaS Subscription (Annual)",
    "Professional Consulting - 10-Hour Package",
    "Standard Cloud Hosting Infrastructure (Monthly)",
    "Custom API Integration Services",
    "Premium Technical Support - Tier 3",
    "Data Migration & System Onboarding Service",
    "Dedicated Technical Account Manager (Annual)",
    "CRM Setup & Configuration Package",
    "Cybersecurity Vulnerability Audit",
    "AI Model Training & Fine-Tuning Consulting"
]


def generate_single_deal(fake: Faker) -> Dict[str, Any]:
    """
    Generate a single realistic closed-won B2B transaction record.

    Args:
        fake: An initialised Faker generator instance.

    Returns:
        A dictionary representing the sales transaction record.
    """
    # Pick a random item description
    item_description = random.choice(B2B_SALES_ITEMS)
    
    # Determine realistic quantity and price based on the item description
    if "Annual" in item_description or "Audit" in item_description:
        quantity = 1
        unit_price = round(random.uniform(5000.00, 25000.00), 2)
    elif "Hour" in item_description:
        quantity = random.choice([5, 10, 20, 50])
        unit_price = 150.00  # Hourly rate
    elif "Monthly" in item_description:
        quantity = random.randint(1, 12)
        unit_price = round(random.uniform(200.00, 800.00), 2)
    else:
        quantity = random.randint(1, 5)
        unit_price = round(random.uniform(1000.00, 4500.00), 2)

    first_name = fake.first_name()
    last_name = fake.last_name()
    company = fake.company()
    
    # Clean company name to create a professional business email
    company_domain = "".join(char for char in company if char.isalnum()).lower()
    email = f"{first_name.lower()}.{last_name.lower()}@{company_domain or 'example'}.com"

    # Generate a target date within the last 90 days (mimicking recent closed-won deals)
    target_date = fake.date_time_between(
        start_date=datetime.now() - timedelta(days=90),
        end_date=datetime.now()
    ).isoformat() + "Z"

    return {
        "transaction_id": fake.uuid4(),
        "customer_first_name": first_name,
        "customer_last_name": last_name,
        "company_name": company,
        "email_address": email,
        "item_description": item_description,
        "quantity": quantity,
        "unit_price": unit_price,
        "target_date": target_date
    }


def generate_batch(count: int = 500) -> List[Dict[str, Any]]:
    """
    Generate a batch list containing a specified number of transaction records.

    Args:
        count: The number of distinct records to generate.

    Returns:
        A list of transaction dictionaries.
    """
    fake = Faker()
    # Seed to allow reproducibility if needed, but keep it dynamic by default.
    return [generate_single_deal(fake) for _ in range(count)]


def stream_deals(count: int = 500, delay_seconds: float = 0.1) -> Generator[Dict[str, Any], None, None]:
    """
    Yield transaction records one by one with a simulated delay to mimic real-time events.

    Args:
        count: Total number of records to stream.
        delay_seconds: Pause interval in seconds between yielded records.

    Yields:
        Transaction dictionaries.
    """
    fake = Faker()
    for _ in range(count):
        yield generate_single_deal(fake)
        if delay_seconds > 0:
            time.sleep(delay_seconds)


def main():
    """CLI execution entrypoint."""
    parser = argparse.ArgumentParser(
        description="Simulate B2B closed-won sales transaction events from monday.com."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=500,
        help="Number of records to generate (default: 500)"
    )
    parser.add_argument(
        "--mode",
        choices=["batch", "stream", "file"],
        default="batch",
        help="Output mode: 'batch' prints JSON array, 'stream' outputs line-by-line, 'file' writes to transactions.json (default: batch)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Stream mode delay in seconds between events (default: 0.05)"
    )
    parser.add_argument(
        "--file-path",
        type=str,
        default="transactions_simulation.json",
        help="Target output file path when mode is 'file' (default: transactions_simulation.json)"
    )

    args = parser.parse_args()

    if args.mode == "batch":
        # Generate the full batch and output as a formatted JSON array to stdout
        deals = generate_batch(args.count)
        print(json.dumps(deals, indent=4))
        
    elif args.mode == "file":
        # Generate full batch and write to a local JSON file
        deals = generate_batch(args.count)
        try:
            with open(args.file_path, "w", encoding="utf-8") as f:
                json.dump(deals, f, indent=4)
            print(f"Successfully generated and wrote {args.count} records to {args.file_path}", file=sys.stderr)
        except OSError as e:
            print(f"Error writing to file: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.mode == "stream":
        # Stream records line by line to stdout to simulate webhooks/live event queues
        print(f"Starting real-time stream of {args.count} transaction records...", file=sys.stderr)
        try:
            for deal in stream_deals(args.count, args.delay):
                print(json.dumps(deal), flush=True)
        except KeyboardInterrupt:
            print("\nStreaming interrupted by user.", file=sys.stderr)


if __name__ == "__main__":
    main()
