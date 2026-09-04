"""
Schema Mapping Configuration Manager.

This module manages loading, validation, and saving of Monday.com to Xero
invoice fields mapping configurations in a local JSON storage file.

Strictly adheres to UK English naming and logging conventions.
"""

import json
import logging
import os
from typing import Dict, List, Tuple

logger = logging.getLogger("ProjectMIRAOrchestrator")

DEFAULT_MAPPING_FILE = "mapping_config.json"

# Core mappings default setup
DEFAULT_MAPPINGS = {
    "transaction_id": "Invoice.Reference",
    "company_name": "Invoice.Contact.Name",
    "customer_first_name": "Invoice.Contact.FirstName",
    "customer_last_name": "Invoice.Contact.LastName",
    "email_address": "Invoice.Contact.EmailAddress",
    "item_description": "Invoice.LineItems[0].Description",
    "quantity": "Invoice.LineItems[0].Quantity",
    "unit_price": "Invoice.LineItems[0].UnitAmount"
}

# Mandatory mappings that cannot be unmapped or saved as empty values
MANDATORY_KEYS = [
    "transaction_id",
    "company_name",
    "email_address",
    "item_description",
    "quantity",
    "unit_price"
]


def load_mapping(config_path: str = DEFAULT_MAPPING_FILE) -> Dict[str, str]:
    """
    Read the active mapping schema from the local storage file.
    If the file does not exist, it falls back to DEFAULT_MAPPINGS and writes it.

    Args:
        config_path: Path to the JSON configuration file.

    Returns:
        A dictionary mapping Monday.com fields to Xero invoice paths.
    """
    if not os.path.exists(config_path):
        logger.info(
            "Configuration file not found at %s. Initialising with default mappings.",
            config_path
        )
        # Write default configuration to disk
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_MAPPINGS, f, indent=4)
        except OSError as e:
            logger.error("Failed to write default mappings configuration: %s", e)
        return DEFAULT_MAPPINGS.copy()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if not isinstance(data, dict):
            logger.warning(
                "Invalid configuration format in %s. Resetting to defaults.",
                config_path
            )
            return DEFAULT_MAPPINGS.copy()
            
        # Ensure all mandatory keys are present (recover missing ones with defaults)
        restored = False
        for key in MANDATORY_KEYS:
            if key not in data or not data[key]:
                data[key] = DEFAULT_MAPPINGS[key]
                restored = True
        
        if restored:
            logger.info("Restored missing mandatory keys in configuration file %s.", config_path)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error(
            "Error reading configuration file at %s: %s. Falling back to defaults.",
            config_path,
            e
        )
        return DEFAULT_MAPPINGS.copy()


def save_mapping(
    mapping_data: Dict[str, str],
    config_path: str = DEFAULT_MAPPING_FILE
) -> Tuple[bool, List[str]]:
    """
    Safely update and overwrite the mapping configuration file.
    Validates presence and non-empty values for mandatory keys.

    Args:
        mapping_data: Proposed mappings dictionary.
        config_path: Path to write the JSON configuration to.

    Returns:
        A tuple of (success_status, list_of_validation_errors).
    """
    validation_errors = []

    if not isinstance(mapping_data, dict):
        return False, ["Invalid configuration payload format. Must be a JSON dictionary."]

    # Validate mandatory fields
    for key in MANDATORY_KEYS:
        if key not in mapping_data:
            validation_errors.append(f"Mandatory field '{key}' is missing from the mapping data.")
        else:
            value = mapping_data[key]
            if not isinstance(value, str) or not value.strip():
                validation_errors.append(
                    f"Mandatory field '{key}' cannot be saved with a blank value."
                )

    if validation_errors:
        logger.warning(
            "Mappings configuration save rejected due to validation errors: %s",
            ", ".join(validation_errors)
        )
        return False, validation_errors

    # Write clean config to storage
    try:
        clean_data = {k: str(v).strip() for k, v in mapping_data.items()}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=4)
        logger.info("Mappings configuration successfully saved to %s.", config_path)
        return True, []
    except OSError as e:
        error_msg = f"Failed to persist mappings configuration: {str(e)}"
        logger.error(error_msg)
        return False, [error_msg]
