"""
Xero Transaction Synchronisation Module.

This module acts as the integration layer between the B2B transaction simulator
and the Xero API. It reads simulated sales events, maps them into standard
Xero accounts receivable invoices (ACCREC), and synchronises them with Xero
using the XeroAuthorisationManager.

All logging, variable naming, and internal logic use strict UK English.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from auth_manager import XeroAuthorisationManager, XeroAuthorisationError

# Logging configuration using UK English terminology
logger = logging.getLogger("XeroTransactionSync")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

XERO_INVOICES_ENDPOINT = "https://api.xero.com/api.xro/2.0/Invoices"
XERO_CONNECTIONS_ENDPOINT = "https://api.xero.com/connections"


class XeroSyncError(Exception):
    """Custom exception raised when synchronisation tasks fail."""
    pass


class XeroTransactionSync:
    """
    Manages mapping B2B transaction records into Xero Invoices and synchronising
    them with the Xero API in efficient batches.
    """

    def __init__(self, auth_manager: XeroAuthorisationManager):
        """
        Initialise the synchronisation manager.

        Args:
            auth_manager: An instance of XeroAuthorisationManager to handle credentials.
        """
        self.auth_manager = auth_manager
        self._tenant_id: Optional[str] = None
        logger.info("XeroTransactionSync pipeline initialised.")

    def get_active_tenant_id(self, force_refresh: bool = False) -> str:
        """
        Retrieve the active Xero tenant ID (connection) for authorisation.
        Queries Xero connections endpoint and returns the first active tenant.

        Args:
            force_refresh: If True, forces querying connections endpoint again.

        Returns:
            The tenant ID string.
        """
        if self._tenant_id and not force_refresh:
            return self._tenant_id

        logger.info("Retrieving active connection details from Xero connections...")
        token = self.auth_manager.get_authorisation_token()
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.get(
                XERO_CONNECTIONS_ENDPOINT,
                headers=headers,
                timeout=15
            )
            response.raise_for_status()
            connections = response.json()

            if not connections:
                raise XeroSyncError(
                    "No active tenant connections found. Please ensure the user has authorised the application."
                )

            # Retrieve the first tenant connection
            tenant_info = connections[0]
            self._tenant_id = tenant_info["tenantId"]
            tenant_name = tenant_info.get("tenantName", "Unknown Organisation")

            logger.info("Found active connection: %s (%s)", tenant_name, self._tenant_id)
            return self._tenant_id

        except requests.exceptions.RequestException as error:
            logger.error("Failed to fetch organisation connections: %s", error)
            raise XeroSyncError(f"Organisation connection retrieval failed: {error}") from error

    def map_transaction_to_invoice(
        self,
        transaction: Dict[str, Any],
        status: str = "DRAFT"
    ) -> Dict[str, Any]:
        """
        Map a single simulated flat B2B transaction into Xero's nested Invoice schema.

        Args:
            transaction: Flat dictionary representation of a transaction deal.
            status: Xero invoice status (typically 'DRAFT' or 'AUTHORISED').

        Returns:
            A dictionary conforming to Xero's nested Invoice schema.
        """
        target_date_str = transaction["target_date"].rstrip("Z")
        try:
            # Parse target date and calculate due date (30 days credit term)
            date_parsed = datetime.fromisoformat(target_date_str)
        except ValueError:
            logger.warning(
                "Could not parse transaction date '%s'. Falling back to current date.",
                target_date_str
            )
            date_parsed = datetime.now(timezone.utc)

        date_formatted = date_parsed.strftime("%Y-%m-%d")
        due_date_formatted = (date_parsed + timedelta(days=30)).strftime("%Y-%m-%d")

        return {
            "Type": "ACCREC",  # Accounts Receivable (Sales Invoice)
            "Contact": {
                "Name": transaction["company_name"],
                "FirstName": transaction["customer_first_name"],
                "LastName": transaction["customer_last_name"],
                "EmailAddress": transaction["email_address"]
            },
            "Date": date_formatted,
            "DueDate": due_date_formatted,
            "Reference": f"TX-{transaction['transaction_id'][:8]}",
            "LineItems": [
                {
                    "Description": transaction["item_description"],
                    "Quantity": transaction["quantity"],
                    "UnitAmount": transaction["unit_price"],
                    "AccountCode": "200"  # Standard code for sales in default chart of accounts
                }
            ],
            "Status": status
        }

    def sync_batch(
        self,
        transactions: List[Dict[str, Any]],
        status: str = "DRAFT",
        chunk_size: int = 50
    ) -> Dict[str, Any]:
        """
        Batch synchronise multiple transaction records to Xero in chunks.

        Args:
            transactions: List of B2B simulated transactions.
            status: Invoice status to assign.
            chunk_size: Number of records to send in each API request payload.

        Returns:
            A summary dictionary containing details about successfully synchronised records.
        """
        if not transactions:
            logger.warning("No transactions provided for synchronisation.")
            return {"successful_count": 0, "failed_count": 0, "details": []}

        # Ensure we have a valid tenant ID to direct API traffic
        tenant_id = self.get_active_tenant_id()
        
        logger.info(
            "Starting synchronisation of %d transactions in chunks of %d...",
            len(transactions),
            chunk_size
        )

        successful_count = 0
        failed_count = 0
        details = []

        # Process transactions in chunks to respect API limits
        for i in range(0, len(transactions), chunk_size):
            chunk = transactions[i : i + chunk_size]
            invoices_payload = [
                self.map_transaction_to_invoice(tx, status) for tx in chunk
            ]

            logger.info(
                "Synchronising chunk %d/%d containing %d invoices...",
                (i // chunk_size) + 1,
                (len(transactions) - 1) // chunk_size + 1,
                len(chunk)
            )

            try:
                response_data = self._post_invoices_to_xero(tenant_id, invoices_payload)
                invoices_returned = response_data.get("Invoices", [])
                successful_count += len(invoices_returned)
                details.extend(invoices_returned)
                
                logger.info(
                    "Successfully synchronised chunk containing %d invoices.",
                    len(invoices_returned)
                )

            except Exception as error:
                failed_count += len(chunk)
                logger.error("Failed to synchronise chunk: %s", error)
                # Continue with the next chunk rather than terminating the whole process
                continue

        logger.info(
            "Synchronisation pipeline finished. Successful invoices: %d, Failed invoices: %d",
            successful_count,
            failed_count
        )

        return {
            "successful_count": successful_count,
            "failed_count": failed_count,
            "details": details
        }

    def _post_invoices_to_xero(
        self,
        tenant_id: str,
        invoices: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Execute POST request to submit invoices payload to Xero API.

        Args:
            tenant_id: Target organisation tenant identifier.
            invoices: List of formatted invoice records.

        Returns:
            JSON response dictionary from the Xero API.
        """
        token = self.auth_manager.get_authorisation_token()
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Xero-tenant-id": tenant_id,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        payload = {"Invoices": invoices}

        try:
            response = requests.post(
                XERO_INVOICES_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as error:
            logger.error("HTTP request to Xero Invoices endpoint failed: %s", error)
            error_details = ""
            if error.response is not None:
                try:
                    error_details = f" - Response body: {error.response.text}"
                except Exception:
                    pass
            raise XeroSyncError(
                f"Failed to publish invoices payload to Xero API: {error}{error_details}"
            ) from error


def main():
    """CLI execution entrypoint for synchronising transactions with Xero."""
    parser = argparse.ArgumentParser(
        description="Synchronise simulated sales deals with Xero as ACCREC invoices."
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=os.environ.get("XERO_CLIENT_ID"),
        help="Xero Client ID (can also be set via XERO_CLIENT_ID env var)"
    )
    parser.add_argument(
        "--client-secret",
        type=str,
        default=os.environ.get("XERO_CLIENT_SECRET"),
        help="Xero Client Secret (can also be set via XERO_CLIENT_SECRET env var)"
    )
    parser.add_argument(
        "--redirect-uri",
        type=str,
        default="https://localhost",
        help="Redirect URI configured in Xero developer dashboard (default: https://localhost)"
    )
    parser.add_argument(
        "--token-storage",
        type=str,
        default="token_storage.json",
        help="Path to token storage JSON file (default: token_storage.json)"
    )
    parser.add_argument(
        "--transactions-file",
        type=str,
        default="transactions_simulation.json",
        help="JSON file containing B2B simulated transactions (default: transactions_simulation.json)"
    )
    parser.add_argument(
        "--status",
        choices=["DRAFT", "SUBMITTED", "AUTHORISED"],
        default="DRAFT",
        help="Invoice status to assign in Xero (default: DRAFT)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="Number of records to sync in each API payload chunk (default: 50)"
    )

    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Error: Both client ID and client secret must be provided either via arguments "
            "(--client-id, --client-secret) or environment variables (XERO_CLIENT_ID, XERO_CLIENT_SECRET).",
            file=sys.stderr
        )
        sys.exit(1)

    if not os.path.exists(args.transactions_file):
        print(
            f"Error: Simulated transactions file not found at: {args.transactions_file}. "
            "Please run 'data_simulation.py --mode file' first.",
            file=sys.stderr
        )
        sys.exit(1)

    try:
        with open(args.transactions_file, "r", encoding="utf-8") as f:
            transactions = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: Failed to read or parse transactions file: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(transactions)} transactions from {args.transactions_file}.", file=sys.stderr)

    try:
        # Initialise authorization manager
        auth_manager = XeroAuthorisationManager(
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            token_storage_path=args.token_storage
        )

        # Initialise transaction sync pipeline
        sync_manager = XeroTransactionSync(auth_manager)
        
        # Start batch synchronisation
        result = sync_manager.sync_batch(
            transactions=transactions,
            status=args.status,
            chunk_size=args.chunk_size
        )

        print("\n=== Synchronisation Run Summary ===", file=sys.stderr)
        print(f"Successfully Synced: {result['successful_count']}", file=sys.stderr)
        print(f"Failed to Sync:      {result['failed_count']}", file=sys.stderr)
        print("===================================", file=sys.stderr)

        if result["failed_count"] > 0:
            sys.exit(1)

    except XeroAuthorisationError as e:
        print(f"Authorisation Error: {e}", file=sys.stderr)
        sys.exit(2)
    except XeroSyncError as e:
        print(f"Synchronisation Error: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
