"""
M.I.R.A. Xero Integration Connector.

Handles API communication with Xero REST API for Contacts, Items, and Invoices.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
import requests

from config import settings
from models.schemas import Contact, Product, Invoice
from utils.logger import logger
from utils.oauth_handler import OAuthHandler


def _format_modified_date(dt: Optional[datetime]) -> Optional[str]:
    """Formats datetime into RFC 2822 HTTP format for Xero If-Modified-Since header."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


class XeroConnector:
    """Connector class for managing interactions with Xero REST API endpoints."""

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        oauth_handler: Optional[OAuthHandler] = None,
    ):
        self.tenant_id = tenant_id or settings.xero_tenant_id
        self.oauth_handler = oauth_handler or OAuthHandler()
        self.base_url = "https://api.xero.com/api.xro/2.0"

    def _get_headers(self) -> Dict[str, str]:
        """Constructs headers required for Xero REST API requests using automatic token refresh."""
        headers = self.oauth_handler.get_bearer_header()
        headers["xero-tenant-id"] = self.tenant_id or ""
        headers["Accept"] = "application/json"
        headers["Content-Type"] = "application/json"
        return headers

    def create_or_update_contact(self, contact_data: Union[Contact, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Creates or updates a contact entry in Xero by calling POST /Contacts.

        Args:
            contact_data: Contact model instance or dictionary.

        Returns:
            Dict[str, Any]: Xero REST API response dictionary.
        """
        url = f"{self.base_url}/Contacts"

        if isinstance(contact_data, Contact):
            payload = contact_data.model_dump(by_alias=True, mode="json")
        elif hasattr(contact_data, "model_dump"):
            payload = contact_data.model_dump(by_alias=True, mode="json")
        elif isinstance(contact_data, dict):
            payload = contact_data
        else:
            payload = dict(contact_data)

        logger.info("Calling Xero REST API POST /Contacts for contact: %s", payload.get("Name"))

        headers = self._get_headers()
        response = requests.post(url, json={"Contacts": [payload]}, headers=headers, timeout=15)
        response.raise_for_status()

        result = response.json()
        logger.info("Xero Contact POST operation succeeded.")
        return result

    def create_product_item(self, item_data: Union[Product, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Creates a product or catalog item entry in Xero by calling POST /Items.

        Args:
            item_data: Product/Item model instance or dictionary.

        Returns:
            Dict[str, Any]: Xero REST API response dictionary.
        """
        url = f"{self.base_url}/Items"

        if isinstance(item_data, Product):
            payload = item_data.model_dump(by_alias=True, mode="json")
        elif hasattr(item_data, "model_dump"):
            payload = item_data.model_dump(by_alias=True, mode="json")
        elif isinstance(item_data, dict):
            payload = item_data
        else:
            payload = dict(item_data)

        logger.info("Calling Xero REST API POST /Items for code: %s", payload.get("Code"))

        headers = self._get_headers()
        response = requests.post(url, json={"Items": [payload]}, headers=headers, timeout=15)
        response.raise_for_status()

        result = response.json()
        logger.info("Xero Item POST operation succeeded.")
        return result

    def create_invoice(self, invoice_data: Union[Invoice, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Creates or updates an invoice in Xero by calling POST /Invoices.

        Args:
            invoice_data: Invoice model instance or dictionary.

        Returns:
            Dict[str, Any]: Xero REST API response dictionary.
        """
        url = f"{self.base_url}/Invoices"

        if isinstance(invoice_data, Invoice):
            payload = invoice_data.model_dump(by_alias=True, mode="json")
        elif hasattr(invoice_data, "model_dump"):
            payload = invoice_data.model_dump(by_alias=True, mode="json")
        elif isinstance(invoice_data, dict):
            payload = invoice_data
        else:
            payload = dict(invoice_data)

        logger.info("Calling Xero REST API POST /Invoices...")

        headers = self._get_headers()
        response = requests.post(url, json={"Invoices": [payload]}, headers=headers, timeout=15)
        response.raise_for_status()

        result = response.json()
        logger.info("Xero Invoice POST operation succeeded.")
        return result

    # Backward compatibility helpers
    def create_contact(self, contact_data: Union[Contact, Dict[str, Any]]) -> Dict[str, Any]:
        return self.create_or_update_contact(contact_data)

    def get_invoices(
        self,
        status: Optional[str] = None,
        page: Optional[int] = None,
        if_modified_since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves list of invoices from Xero, optionally filtered by status, page, and updated date."""
        url = f"{self.base_url}/Invoices"
        params = {}
        if status:
            params["Statuses"] = status
        if page is not None:
            params["page"] = page

        headers = self._get_headers()
        mod_since_hdr = _format_modified_date(if_modified_since)
        if mod_since_hdr:
            headers["If-Modified-Since"] = mod_since_hdr

        logger.info("Fetching invoices from Xero...")

        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json().get("Invoices", [])

    def get_contacts(self, if_modified_since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Retrieves list of contacts from Xero, optionally filtered by updated date."""
        url = f"{self.base_url}/Contacts"
        headers = self._get_headers()
        mod_since_hdr = _format_modified_date(if_modified_since)
        if mod_since_hdr:
            headers["If-Modified-Since"] = mod_since_hdr

        logger.info("Fetching contacts from Xero...")

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("Contacts", [])

    def get_items(self, if_modified_since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Retrieves list of items/products from Xero, optionally filtered by updated date."""
        url = f"{self.base_url}/Items"
        headers = self._get_headers()
        mod_since_hdr = _format_modified_date(if_modified_since)
        if mod_since_hdr:
            headers["If-Modified-Since"] = mod_since_hdr

        logger.info("Fetching items from Xero...")

        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            logger.error("Xero Items API error: %s - %s", response.status_code, response.text)
        response.raise_for_status()
        return response.json().get("Items", [])

    def get_invoice_by_id(self, invoice_id: str) -> Dict[str, Any]:
        """Retrieves a single detailed invoice from Xero by ID (including LineItems)."""
        url = f"{self.base_url}/Invoices/{invoice_id}"
        headers = self._get_headers()
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        invs = response.json().get("Invoices", [])
        return invs[0] if invs else {}

