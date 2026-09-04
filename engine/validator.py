"""
M.I.R.A. Data Validation Engine.

Provides active validation for incoming payloads from webhooks or connectors prior to transformation.
"""

import re
from datetime import datetime
from typing import Any, Dict, List
from utils.logger import logger

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class DataValidator:
    """Validates raw incoming data structures for integration workflows."""

    @staticmethod
    def validate_monday_record(record: Dict[str, Any]) -> List[str]:
        """
        Validates raw flat Monday transaction records.

        Args:
            record: Dictionary of incoming transaction data.

        Returns:
            List[str]: List of error strings. Empty list indicates record is valid.
        """
        errors = []
        required_fields = [
            "transaction_id",
            "customer_first_name",
            "customer_last_name",
            "company_name",
            "email_address",
            "item_description",
            "quantity",
            "unit_price",
            "target_date",
        ]

        for field in required_fields:
            if field not in record or record[field] is None:
                errors.append(f"Missing required field: '{field}'")
                continue
            if isinstance(record[field], str) and not record[field].strip():
                errors.append(f"Field '{field}' cannot be blank")

        if errors:
            return errors

        # Validate Email
        email = str(record["email_address"]).strip()
        if not EMAIL_REGEX.match(email):
            errors.append(f"Invalid email address format: '{email}'")

        # Validate Quantity
        qty = record["quantity"]
        if not isinstance(qty, int) or qty <= 0:
            errors.append(f"Quantity must be a positive integer, got: {qty}")

        # Validate Unit Price
        price = record["unit_price"]
        if not isinstance(price, (int, float)) or price <= 0:
            errors.append(f"Unit price must be a positive number, got: {price}")

        # Validate Target Date
        date_str = str(record["target_date"]).rstrip("Z")
        try:
            datetime.fromisoformat(date_str)
        except ValueError:
            errors.append(f"Invalid ISO 8601 target_date string: '{record['target_date']}'")

        if errors:
            logger.warning(
                "Validation failed for record ID %s: %s",
                record.get("transaction_id", "unknown"),
                ", ".join(errors),
            )

        return errors
