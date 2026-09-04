"""
M.I.R.A. Data Transformation Engine.

Translates Monday.com payloads into structured enterprise schemas (Contact, Product, LineItem, Invoice)
and Xero API payload structures.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pydantic import ValidationError

from models.schemas import Contact, Product, LineItem, Invoice
from models.invoice import InvoiceStatus, InvoiceBatchResponse
from engine.validator import DataValidator
from utils.logger import logger


class TransformerValidationError(ValueError):
    """Custom exception raised when Monday payload validation fails prior to Xero submission."""

    pass


def _extract_monday_field(payload: Dict[str, Any], candidate_keys: List[str]) -> Optional[Any]:
    """
    Safely extracts a field value from a Monday item dictionary using list of candidate keys.
    Loops through candidate keys sequentially. For each candidate key, it tries:
    1. Direct key lookup in target dictionaries.
    2. Column values array lookup in target dictionaries.
    """
    if not isinstance(payload, dict):
        return None

    # Check payload['event'] if present
    target_dicts = [payload]
    if isinstance(payload.get("event"), dict):
        target_dicts.append(payload["event"])

    for key in candidate_keys:
        k_lower = key.lower()
        for d in target_dicts:
            # 1. Direct key lookup
            if key in d and d[key] is not None and str(d[key]).strip() != "":
                return d[key]

            # 2. Column values lookup
            col_list = d.get("column_values") or d.get("columnValues")
            if isinstance(col_list, list):
                for col in col_list:
                    if not isinstance(col, dict):
                        continue
                    col_id = str(col.get("id", "")).lower()
                    col_title = str(col.get("title", "")).lower()

                    if k_lower == col_id or k_lower in col_title:
                        if col.get("text") is not None and str(col["text"]).strip():
                            return col["text"]
                        if col.get("email") is not None and str(col["email"]).strip():
                            return col["email"]
                        if col.get("value") is not None and str(col["value"]).strip():
                            val = str(col["value"]).strip()
                            if val.startswith('"') and val.endswith('"'):
                                val = val.strip('"')
                            
                            # Check if it's a JSON object representing a phone number
                            if val.startswith("{") and val.endswith("}"):
                                try:
                                    import json
                                    val_dict = json.loads(val)
                                    if isinstance(val_dict, dict) and "phone" in val_dict:
                                        p_num = str(val_dict["phone"]).strip()
                                        country_code = str(val_dict.get("countryShortName", "")).strip().upper()
                                        
                                        # Prepend country code prefix if it's not already prefixed with +
                                        if p_num and not p_num.startswith("+"):
                                            country_codes = {
                                                "GB": "+44", "US": "+1", "CA": "+1", "AU": "+61", "NZ": "+64",
                                                "FR": "+33", "DE": "+49", "IT": "+39", "ES": "+34", "IE": "+353",
                                                "ZA": "+27", "IN": "+91", "CN": "+86", "JP": "+81", "BR": "+55"
                                            }
                                            prefix = country_codes.get(country_code, "")
                                            if prefix:
                                                p_num = f"{prefix}{p_num}"
                                        return p_num
                                except Exception:
                                    pass
                            return val

    return None


def monday_item_to_xero_contact(
    monday_payload: Dict[str, Any],
    mappings: Optional[Dict[str, str]] = None,
    customer_mappings: Optional[Dict[str, str]] = None,
) -> Contact:
    """
    Extracts client details from a Monday item/row payload and validates them into a Contact model.
    Supports linked customer board item via Connect Boards relation.

    Args:
        monday_payload: Raw Monday.com item dictionary.
        mappings: User-defined schema mappings dictionary.
        customer_mappings: Optional customer board mappings dictionary.

    Returns:
        Contact: Pydantic Contact model instance.

    Raises:
        TransformerValidationError: If required fields (e.g. Email or Name) are missing or invalid.
    """
    item_id = monday_payload.get("id") or monday_payload.get("transaction_id") or "unknown"

    # Resolve linked customer if it exists in a Connect Boards column
    linked_customer = None
    customer_col_id = mappings.get("Invoice.Contact.Name") if mappings else None
    
    col_list = monday_payload.get("column_values") or monday_payload.get("columnValues")
    if isinstance(col_list, list):
        if customer_col_id:
            for col in col_list:
                if str(col.get("id", "")).lower() == customer_col_id.lower():
                    linked = col.get("linked_items")
                    if isinstance(linked, list) and len(linked) > 0:
                        linked_customer = linked[0]
                        break
        
        if not linked_customer:
            # Fallback scan for product/item column containing linked_items
            for col in col_list:
                col_title = str(col.get("title", "")).lower()
                if "customer" in col_title or "client" in col_title or "contact" in col_title:
                    linked = col.get("linked_items")
                    if isinstance(linked, list) and len(linked) > 0:
                        linked_customer = linked[0]
                        break

    target_payload = linked_customer if linked_customer else monday_payload
    active_mappings = customer_mappings if linked_customer else mappings

    name_candidates = ["company_name", "company", "name", "customer_name", "client_name", "pulseName"]
    if active_mappings and active_mappings.get("Invoice.Contact.Name"):
        name_candidates.insert(0, active_mappings["Invoice.Contact.Name"])

    email_candidates = ["email_address", "email", "customer_email", "client_email"]
    if active_mappings and active_mappings.get("Invoice.Contact.EmailAddress"):
        email_candidates.insert(0, active_mappings["Invoice.Contact.EmailAddress"])

    first_name_candidates = ["customer_first_name", "first_name", "firstname"]
    if active_mappings and active_mappings.get("Invoice.Contact.FirstName"):
        first_name_candidates.insert(0, active_mappings["Invoice.Contact.FirstName"])

    last_name_candidates = ["customer_last_name", "last_name", "lastname"]
    if active_mappings and active_mappings.get("Invoice.Contact.LastName"):
        last_name_candidates.insert(0, active_mappings["Invoice.Contact.LastName"])

    phone_candidates = ["phone", "phone_number", "mobile", "telephone"]
    if active_mappings and active_mappings.get("Invoice.Contact.Phone"):
        phone_candidates.insert(0, active_mappings["Invoice.Contact.Phone"])

    account_candidates = ["account_number", "account_num", "account_code", "customer_id"]
    if active_mappings and active_mappings.get("Invoice.Contact.AccountNumber"):
        account_candidates.insert(0, active_mappings["Invoice.Contact.AccountNumber"])

    contact_id_candidates = ["contact_id", "contactid", "id", "customer_id", "client_id"]
    if active_mappings and active_mappings.get("Invoice.Contact.ContactID"):
        contact_id_candidates.insert(0, active_mappings["Invoice.Contact.ContactID"])

    address_candidates = ["address", "street", "location", "address_line_1", "address_line1"]
    if active_mappings and active_mappings.get("Invoice.Contact.Address"):
        address_candidates.insert(0, active_mappings["Invoice.Contact.Address"])

    name = _extract_monday_field(target_payload, name_candidates)
    email = _extract_monday_field(target_payload, email_candidates)
    first_name = _extract_monday_field(target_payload, first_name_candidates)
    last_name = _extract_monday_field(target_payload, last_name_candidates)
    phone = _extract_monday_field(target_payload, phone_candidates)
    account_number = _extract_monday_field(target_payload, account_candidates)
    contact_id = _extract_monday_field(target_payload, contact_id_candidates)
    address = _extract_monday_field(target_payload, address_candidates)

    if linked_customer:
        # Fallback to parent payload name/email if not resolved in linked customer
        if not name:
            name_parent_candidates = ["company_name", "company", "name", "customer_name", "client_name", "pulseName"]
            if mappings and mappings.get("Invoice.Contact.Name"):
                name_parent_candidates.insert(0, mappings["Invoice.Contact.Name"])
            name = _extract_monday_field(monday_payload, name_parent_candidates)
            if not name:
                name = linked_customer.get("name") or monday_payload.get("name")
        if not email:
            email_parent_candidates = ["email_address", "email", "customer_email", "client_email"]
            if mappings and mappings.get("Invoice.Contact.EmailAddress"):
                email_parent_candidates.insert(0, mappings["Invoice.Contact.EmailAddress"])
            email = _extract_monday_field(monday_payload, email_parent_candidates)
        if not first_name:
            first_name = _extract_monday_field(monday_payload, first_name_candidates)
        if not last_name:
            last_name = _extract_monday_field(monday_payload, last_name_candidates)
        if not phone:
            phone = _extract_monday_field(monday_payload, phone_candidates)
        if not account_number:
            account_number = _extract_monday_field(monday_payload, account_candidates)
        if not contact_id:
            contact_id = _extract_monday_field(monday_payload, contact_id_candidates)
        if not address:
            address = _extract_monday_field(monday_payload, address_candidates)

    if not name:
        err_msg = f"Validation Error: Missing required Contact Name in Monday payload (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    if not email:
        err_msg = f"Validation Error: Missing required Contact Email in Monday payload (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    try:
        contact = Contact(
            name=str(name).strip(),
            email=str(email).strip(),
            first_name=str(first_name).strip() if first_name else None,
            last_name=str(last_name).strip() if last_name else None,
            phone=str(phone).strip() if phone else None,
            account_number=str(account_number).strip() if account_number else None,
            contact_id=str(contact_id).strip() if contact_id else None,
            address=str(address).strip() if address else None,
        )
        logger.info("Successfully mapped Monday item to Xero Contact: %s (%s)", contact.Name, contact.Email)
        return contact
    except ValidationError as ve:
        err_msg = f"Validation Error: Invalid Contact data for item_id {item_id}: {ve}"
        logger.error(err_msg)
        raise TransformerValidationError(err_msg) from ve


def monday_item_to_xero_product(monday_payload: Dict[str, Any], mappings: Optional[Dict[str, str]] = None) -> Product:
    """
    Extracts item catalog data from a Monday item/row payload and validates them into a Product model.

    Args:
        monday_payload: Raw Monday.com item dictionary.
        mappings: User-defined schema mappings dictionary.

    Returns:
        Product: Pydantic Product model instance.

    Raises:
        TransformerValidationError: If required fields (e.g. Code, Price) are missing or invalid.
    """
    item_id = monday_payload.get("id") or monday_payload.get("transaction_id") or "unknown"

    code_candidates = ["item_code", "code", "sku", "product_code", "item_id", "id"]
    if mappings and mappings.get("Invoice.LineItems[0].ItemCode"):
        code_candidates.insert(0, mappings["Invoice.LineItems[0].ItemCode"])

    name_candidates = ["item_name", "product_name", "item_description", "description", "name", "title", "pulseName"]
    if mappings and mappings.get("Invoice.LineItems[0].Description"):
        name_candidates.insert(0, mappings["Invoice.LineItems[0].Description"])

    price_candidates = ["unit_price", "price", "unit_amount", "amount", "rate", "cost"]
    if mappings and mappings.get("Invoice.LineItems[0].UnitAmount"):
        price_candidates.insert(0, mappings["Invoice.LineItems[0].UnitAmount"])

    desc_candidates = ["item_description", "description", "details", "notes"]

    code = _extract_monday_field(monday_payload, code_candidates)
    name = _extract_monday_field(monday_payload, name_candidates)
    price_val = _extract_monday_field(monday_payload, price_candidates)
    description = _extract_monday_field(monday_payload, desc_candidates)

    if not code:
        err_msg = f"Validation Error: Missing required Product Code in Monday payload (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    if not name:
        err_msg = f"Validation Error: Missing required Product Name in Monday payload (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    if price_val is None:
        err_msg = f"Validation Error: Missing required Product UnitPrice in Monday payload (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    try:
        unit_price = float(price_val)
        if unit_price <= 0:
            raise ValueError(f"Price must be greater than 0, got {unit_price}")
    except (ValueError, TypeError) as pe:
        err_msg = f"Validation Error: Invalid Product UnitPrice '{price_val}' for item_id {item_id}: {pe}"
        logger.error(err_msg)
        raise TransformerValidationError(err_msg) from pe

    try:
        product = Product(
            code=str(code).strip(),
            name=str(name).strip(),
            unit_price=unit_price,
            description=str(description).strip() if description else None,
        )
        logger.info("Successfully mapped Monday item to Xero Product: %s (%s)", product.Code, product.Name)
        return product
    except ValidationError as ve:
        err_msg = f"Validation Error: Invalid Product data for item_id {item_id}: {ve}"
        logger.error(err_msg)
        raise TransformerValidationError(err_msg) from ve


def monday_items_to_xero_invoice(
    monday_parent_item: Dict[str, Any],
    monday_subitems: Optional[List[Dict[str, Any]]] = None,
    mappings: Optional[Dict[str, str]] = None,
    customer_mappings: Optional[Dict[str, str]] = None,
    product_mappings: Optional[Dict[str, str]] = None,
) -> Invoice:
    """
    Bundles a Monday parent item and optional subitems into a multi-line item Xero Invoice.

    Args:
        monday_parent_item: Parent Monday item containing client contact details & metadata.
        monday_subitems: List of subitem records representing individual invoice line items.
        mappings: User-defined schema mappings dictionary.
        customer_mappings: Optional customer board mappings dictionary.
        product_mappings: Optional product board mappings dictionary.

    Returns:
        Invoice: Pydantic Invoice model instance ready for Xero submission.

    Raises:
        TransformerValidationError: If required fields or valid line items are missing.
    """
    item_id = monday_parent_item.get("id") or monday_parent_item.get("transaction_id") or "unknown"
    logger.info("Starting transformation of Monday items to Xero Invoice (item_id: %s)", item_id)

    # 1. Extract and validate Contact
    contact = monday_item_to_xero_contact(monday_parent_item, mappings=mappings, customer_mappings=customer_mappings)

    # 2. Determine raw line item records
    raw_lines: List[Dict[str, Any]] = []

    if monday_subitems and isinstance(monday_subitems, list) and len(monday_subitems) > 0:
        raw_lines = monday_subitems
    else:
        # Check if parent payload embeds subitems or line_items
        embedded = monday_parent_item.get("subitems") or monday_parent_item.get("line_items")
        if isinstance(embedded, list) and len(embedded) > 0:
            raw_lines = embedded
        else:
            # Fallback to treating parent item as a single line item
            raw_lines = [monday_parent_item]

    # 3. Process & validate line items
    line_items: List[LineItem] = []
    for idx, line_payload in enumerate(raw_lines, start=1):
        code_candidates = ["item_code", "code", "sku", "product_code", "id"]
        if mappings and mappings.get("Invoice.LineItems[0].ItemCode"):
            code_candidates.insert(0, mappings["Invoice.LineItems[0].ItemCode"])

        desc_candidates = ["item_description", "description", "item_name", "name", "title"]
        if mappings and mappings.get("Invoice.LineItems[0].Description"):
            desc_candidates.insert(0, mappings["Invoice.LineItems[0].Description"])

        qty_candidates = ["quantity", "qty", "count"]
        if mappings and mappings.get("Invoice.LineItems[0].Quantity"):
            qty_candidates.insert(0, mappings["Invoice.LineItems[0].Quantity"])

        price_candidates = ["unit_price", "price", "unit_amount", "amount", "rate", "cost"]
        if mappings and mappings.get("Invoice.LineItems[0].UnitAmount"):
            price_candidates.insert(0, mappings["Invoice.LineItems[0].UnitAmount"])

        tax_candidates = ["tax_type", "tax_rate", "tax"]
        if mappings and mappings.get("Invoice.LineItems[0].TaxType"):
            tax_candidates.insert(0, mappings["Invoice.LineItems[0].TaxType"])

        # Check if this line item has a linked product item via Connect Boards
        linked_product = None
        product_col_id = mappings.get("Invoice.LineItems[0].ItemCode") if mappings else None
        
        col_list = line_payload.get("column_values") or line_payload.get("columnValues")
        if isinstance(col_list, list):
            if product_col_id:
                for col in col_list:
                    if str(col.get("id", "")).lower() == product_col_id.lower():
                        linked = col.get("linked_items")
                        if isinstance(linked, list) and len(linked) > 0:
                            linked_product = linked[0]
                            break
            
            if not linked_product:
                # Fallback scan for product/item column containing linked_items
                for col in col_list:
                    col_title = str(col.get("title", "")).lower()
                    if "product" in col_title or "item" in col_title:
                        linked = col.get("linked_items")
                        if isinstance(linked, list) and len(linked) > 0:
                            linked_product = linked[0]
                            break

        if linked_product:
            prod_code_candidates = ["item_number", "code", "sku", "product_code", "id"]
            prod_name_candidates = ["item_name", "product_name", "description", "name", "title"]
            prod_desc_candidates = ["description", "details", "notes"]
            prod_price_candidates = ["selling_price", "price", "unit_amount", "amount", "rate", "cost"]
            
            if product_mappings:
                if product_mappings.get("Item.Code"):
                    prod_code_candidates.insert(0, product_mappings["Item.Code"])
                if product_mappings.get("Item.Name"):
                    prod_name_candidates.insert(0, product_mappings["Item.Name"])
                if product_mappings.get("Item.Description"):
                    prod_desc_candidates.insert(0, product_mappings["Item.Description"])
                if product_mappings.get("Item.SalesDetails.UnitPrice"):
                    prod_price_candidates.insert(0, product_mappings["Item.SalesDetails.UnitPrice"])
            
            line_item_code = _extract_monday_field(linked_product, prod_code_candidates) or _extract_monday_field(line_payload, code_candidates) or linked_product.get("name")
            line_desc = _extract_monday_field(linked_product, prod_desc_candidates) or _extract_monday_field(linked_product, prod_name_candidates) or _extract_monday_field(line_payload, desc_candidates) or "Service / Product line item"
            price_val = _extract_monday_field(linked_product, prod_price_candidates)
            qty_val = _extract_monday_field(line_payload, qty_candidates) or 1
            tax_type = _extract_monday_field(line_payload, tax_candidates) or "OUTPUT2"
            
            if price_val is None:
                price_val = _extract_monday_field(line_payload, price_candidates)
        else:
            line_item_code = _extract_monday_field(line_payload, code_candidates) or f"ITEM-{idx:03d}"
            line_desc = _extract_monday_field(line_payload, desc_candidates) or "Service / Product line item"
            qty_val = _extract_monday_field(line_payload, qty_candidates) or 1
            price_val = _extract_monday_field(line_payload, price_candidates)
            tax_type = _extract_monday_field(line_payload, tax_candidates) or "OUTPUT2"

        if price_val is None:
            err_msg = (
                f"Validation Error: Missing UnitPrice for line item #{idx} in invoice (item_id: {item_id})."
            )
            logger.error(err_msg)
            raise TransformerValidationError(err_msg)

        try:
            quantity = float(qty_val)
            unit_amount = float(price_val)
            if quantity <= 0 or unit_amount <= 0:
                raise ValueError(f"Quantity and UnitAmount must be > 0. Got qty={quantity}, price={unit_amount}")
        except (ValueError, TypeError) as pe:
            err_msg = f"Validation Error: Invalid numeric values for line item #{idx} (item_id: {item_id}): {pe}"
            logger.error(err_msg)
            raise TransformerValidationError(err_msg) from pe

        try:
            line_item = LineItem(
                item_code=str(line_item_code).strip(),
                quantity=quantity,
                unit_amount=unit_amount,
                tax_type=str(tax_type).strip(),
                description=str(line_desc).strip(),
            )
            line_items.append(line_item)
        except ValidationError as ve:
            err_msg = f"Validation Error: Line item #{idx} validation failed (item_id: {item_id}): {ve}"
            logger.error(err_msg)
            raise TransformerValidationError(err_msg) from ve

    if not line_items:
        err_msg = f"Validation Error: No valid line items could be built for invoice (item_id: {item_id})."
        logger.error(err_msg)
        raise TransformerValidationError(err_msg)

    # 4. Extract dates
    date_candidates = ["target_date", "issue_date", "date", "created_at"]
    due_candidates = ["due_date", "payment_due", "target_due_date"]
    
    raw_target_date = _extract_monday_field(monday_parent_item, date_candidates)
    raw_due_date = _extract_monday_field(monday_parent_item, due_candidates)

    issue_date = datetime.now().strftime("%Y-%m-%d")
    if raw_target_date:
        try:
            parsed = datetime.fromisoformat(str(raw_target_date).rstrip("Z"))
            issue_date = parsed.strftime("%Y-%m-%d")
        except ValueError:
            logger.warning("Could not parse issue date '%s', defaulting to today.", raw_target_date)

    due_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    if raw_due_date:
        try:
            parsed = datetime.fromisoformat(str(raw_due_date).rstrip("Z"))
            due_date = parsed.strftime("%Y-%m-%d")
        except ValueError:
            logger.warning("Could not parse due date '%s', defaulting to +30 days.", raw_due_date)

    try:
        invoice = Invoice(
            contact=contact,
            line_items=line_items,
            issue_date=issue_date,
            due_date=due_date,
            status="DRAFT",
        )
        logger.info(
            "Successfully created Xero Invoice with %d line item(s) totalling £%.2f for contact '%s'.",
            len(invoice.LineItems),
            invoice.TotalAmount,
            contact.Name,
        )
        return invoice
    except ValidationError as ve:
        err_msg = f"Validation Error: Invoice assembly failed (item_id: {item_id}): {ve}"
        logger.error(err_msg)
        raise TransformerValidationError(err_msg) from ve


class DataTransformer:
    """
    Middleware engine parsing, validating, and converting flat transaction feeds
    into structured Pydantic domain models and nested target payloads.
    """

    def __init__(self, default_account_code: str = "200"):
        self.default_account_code = default_account_code
        self.validator = DataValidator()

    def transform_record_to_invoice(
        self,
        record: Dict[str, Any],
        status: InvoiceStatus = InvoiceStatus.DRAFT,
        mappings: Optional[Dict[str, str]] = None,
        customer_mappings: Optional[Dict[str, str]] = None,
        product_mappings: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Transforms a single raw transaction record into an Invoice model.
        """
        try:
            invoice = monday_items_to_xero_invoice(
                record,
                mappings=mappings,
                customer_mappings=customer_mappings,
                product_mappings=product_mappings,
            )
            return {"status": "success", "invoice": invoice}
        except TransformerValidationError as e:
            return {
                "status": "validation_failed",
                "record_id": record.get("transaction_id") or record.get("id") or "unknown",
                "validation_errors": [str(e)],
                "original_record": record,
            }

    def transform_batch_grouped(
        self,
        records: List[Dict[str, Any]],
        status: InvoiceStatus = InvoiceStatus.DRAFT,
        mappings: Optional[Dict[str, str]] = None,
        customer_mappings: Optional[Dict[str, str]] = None,
        product_mappings: Optional[Dict[str, str]] = None,
    ) -> InvoiceBatchResponse:
        """
        Transforms multiple flat records, grouping items by company to produce multi-line invoices.
        """
        grouped_transactions: Dict[str, List[Dict[str, Any]]] = {}
        validation_failures: List[Dict[str, Any]] = []

        for record in records:
            try:
                # Check basic contact extraction to obtain company name
                contact = monday_item_to_xero_contact(record, mappings=mappings, customer_mappings=customer_mappings)
                company = contact.Name
                if company not in grouped_transactions:
                    grouped_transactions[company] = []
                grouped_transactions[company].append(record)
            except TransformerValidationError as e:
                validation_failures.append(
                    {
                        "status": "validation_failed",
                        "record_id": record.get("transaction_id") or record.get("id") or "unknown",
                        "validation_errors": [str(e)],
                        "original_record": record,
                    }
                )

        transformed_invoices: List[Any] = []

        for company, tx_list in grouped_transactions.items():
            primary_tx = tx_list[0]
            try:
                invoice = monday_items_to_xero_invoice(
                    primary_tx,
                    monday_subitems=tx_list,
                    mappings=mappings,
                    customer_mappings=customer_mappings,
                    product_mappings=product_mappings,
                )
                transformed_invoices.append(invoice)
            except TransformerValidationError as e:
                validation_failures.append(
                    {
                        "status": "validation_failed",
                        "record_id": primary_tx.get("transaction_id") or primary_tx.get("id") or "unknown",
                        "validation_errors": [str(e)],
                        "original_record": primary_tx,
                    }
                )

        logger.info(
            "Batch transformation completed: %d invoices generated, %d records failed validation.",
            len(transformed_invoices),
            len(validation_failures),
        )

        return InvoiceBatchResponse(
            status="success" if not validation_failures else "partial_success",
            transformed_invoices=transformed_invoices,
            failed_records=validation_failures,
        )

def xero_contact_to_monday_item(
    xero_contact: Dict[str, Any],
    mappings: Optional[Dict[str, str]] = None,
    column_types: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Transforms a Xero contact record into a Monday.com item payload (item_name and column_values).
    """
    name = xero_contact.get("Name") or xero_contact.get("name") or "Unknown Xero Contact"
    email = xero_contact.get("EmailAddress") or xero_contact.get("email") or ""
    first_name = xero_contact.get("FirstName") or xero_contact.get("first_name") or ""
    last_name = xero_contact.get("LastName") or xero_contact.get("last_name") or ""
    
    phone = ""
    phones = xero_contact.get("Phones") or xero_contact.get("phones")
    if isinstance(phones, list) and len(phones) > 0:
        parsed_candidates = []
        for p in phones:
            num = p.get("PhoneNumber") or p.get("phone_number")
            if num:
                country = p.get("PhoneCountryCode") or ""
                area = p.get("PhoneAreaCode") or ""
                p_type = (p.get("PhoneType") or p.get("phone_type") or "DEFAULT").upper()
                
                parts = []
                if country:
                    c_clean = str(country).strip().replace("+", "").replace("(", "").replace(")", "")
                    parts.append(f"+{c_clean}")
                if area:
                    parts.append(str(area).strip())
                parts.append(str(num).strip())
                full_num = " ".join([pt for pt in parts if pt])
                
                # Calculate ranking score:
                # Completeness score: 2 points for country, 1 point for area
                completeness_score = (2 if country else 0) + (1 if area else 0)
                # Type priority score: MOBILE (4), DEFAULT (3), DDI (2), FAX (1), others (0)
                type_priority = {"MOBILE": 4, "DEFAULT": 3, "DDI": 2, "FAX": 1}.get(p_type, 0)
                
                # Overall score = completeness_score * 10 + type_priority
                score = completeness_score * 10 + type_priority
                parsed_candidates.append((score, full_num))
        
        if parsed_candidates:
            # Sort candidates by score descending
            parsed_candidates.sort(key=lambda x: x[0], reverse=True)
            phone = parsed_candidates[0][1]
    if not phone:
        phone = xero_contact.get("Phone") or xero_contact.get("phone") or ""
        
    account_number = xero_contact.get("AccountNumber") or xero_contact.get("account_number") or ""
    contact_id = xero_contact.get("ContactID") or xero_contact.get("contact_id") or ""
    
    # Process address (either flat field or nested Addresses list from Xero API)
    address = xero_contact.get("Address") or xero_contact.get("address") or ""
    if not address:
        addresses = xero_contact.get("Addresses") or xero_contact.get("addresses")
        if isinstance(addresses, list) and len(addresses) > 0:
            street_address = next((a for a in addresses if a.get("AddressType") == "STREET"), addresses[0])
            parts = [
                street_address.get("AddressLine1"),
                street_address.get("AddressLine2"),
                street_address.get("City"),
                street_address.get("Region"),
                street_address.get("PostalCode"),
                street_address.get("Country")
            ]
            address = ", ".join([p for p in parts if p])
 
    column_values = {}
    
    # Resolve column IDs from mappings or use standard fallback names
    email_col = "email"
    if mappings and mappings.get("Invoice.Contact.EmailAddress"):
        email_col = mappings["Invoice.Contact.EmailAddress"]
        
    phone_col = "phone"
    if mappings and mappings.get("Invoice.Contact.Phone"):
        phone_col = mappings["Invoice.Contact.Phone"]
        
    account_col = "account_number"
    if mappings and mappings.get("Invoice.Contact.AccountNumber"):
        account_col = mappings["Invoice.Contact.AccountNumber"]
 
    contact_id_col = "contact_id"
    if mappings and mappings.get("Invoice.Contact.ContactID"):
        contact_id_col = mappings["Invoice.Contact.ContactID"]
 
    address_col = "address"
    if mappings and mappings.get("Invoice.Contact.Address"):
        address_col = mappings["Invoice.Contact.Address"]
        
    if email_col and email_col != "pulseName":
        column_values[email_col] = {"email": email, "text": email}
        
    if phone_col and phone_col != "pulseName":
        col_type = None
        if column_types:
            col_type = column_types.get(phone_col)
            
        if col_type == "phone":
            phone_clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            column_values[phone_col] = {"phone": phone_clean}
        elif col_type == "text":
            column_values[phone_col] = phone
        else:
            col_lower = phone_col.lower()
            if "phone" in col_lower or "tel" in col_lower or "mob" in col_lower or "fax" in col_lower:
                # Clean formatting for Monday's phone column payload format
                phone_clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                column_values[phone_col] = {"phone": phone_clean}
            else:
                column_values[phone_col] = phone
        
    if account_col and account_col != "pulseName":
        column_values[account_col] = account_number

    if contact_id_col and contact_id_col != "pulseName":
        column_values[contact_id_col] = contact_id

    if address_col and address_col != "pulseName":
        column_values[address_col] = address
        
    first_name_col = "first_name"
    if mappings and mappings.get("Invoice.Contact.FirstName"):
        first_name_col = mappings["Invoice.Contact.FirstName"]
        
    last_name_col = "last_name"
    if mappings and mappings.get("Invoice.Contact.LastName"):
        last_name_col = mappings["Invoice.Contact.LastName"]
        
    if first_name_col and first_name_col != "pulseName":
        column_values[first_name_col] = first_name
        
    if last_name_col and last_name_col != "pulseName":
        column_values[last_name_col] = last_name
        
    return {
        "item_name": name,
        "column_values": column_values
    }


def xero_invoice_to_monday_item(xero_invoice: Dict[str, Any], mappings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Transforms a Xero invoice record into a Monday.com item payload (item_name and column_values).
    """
    invoice_number = xero_invoice.get("InvoiceNumber") or xero_invoice.get("invoice_number") or f"INV-{int(time.time())}"
    contact = xero_invoice.get("Contact") or {}
    contact_name = contact.get("Name") or "Unknown Contact"
    
    total = xero_invoice.get("Total") or xero_invoice.get("total") or 0.0
    status = xero_invoice.get("Status") or xero_invoice.get("status") or "DRAFT"
    
    column_values = {}
    
    # Simple default mappings for invoice details on Monday columns
    column_values["client_name"] = contact_name
    column_values["amount"] = float(total)
    column_values["status"] = status
    
    return {
        "item_name": invoice_number,
        "column_values": column_values
    }


def xero_item_to_monday_product(
    xero_item: Dict[str, Any],
    mappings: Optional[Dict[str, str]] = None,
    column_types: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Transforms a Xero item/product record into a Monday.com item payload (item_name and column_values).
    """
    name = xero_item.get("Name") or xero_item.get("name") or "Unknown Xero Product"
    code = xero_item.get("Code") or xero_item.get("code") or ""
    description = xero_item.get("Description") or xero_item.get("description") or ""
    
    purchase_details = xero_item.get("PurchaseDetails") or {}
    sales_details = xero_item.get("SalesDetails") or {}
    
    cost_price = purchase_details.get("UnitPrice") or purchase_details.get("unit_price") or 0.0
    selling_price = sales_details.get("UnitPrice") or sales_details.get("unit_price") or 0.0
    
    column_values = {}
    
    name_col = "item_name"
    if mappings and mappings.get("Item.Name"):
        name_col = mappings["Item.Name"]
        
    code_col = "item_number"
    if mappings and mappings.get("Item.Code"):
        code_col = mappings["Item.Code"]
        
    desc_col = "description"
    if mappings and mappings.get("Item.Description"):
        desc_col = mappings["Item.Description"]
        
    cost_col = "cost_price"
    if mappings and mappings.get("Item.PurchaseDetails.UnitPrice"):
        cost_col = mappings["Item.PurchaseDetails.UnitPrice"]
        
    sell_col = "selling_price"
    if mappings and mappings.get("Item.SalesDetails.UnitPrice"):
        sell_col = mappings["Item.SalesDetails.UnitPrice"]
        
    if code_col and code_col != "pulseName":
        column_values[code_col] = code
        
    if desc_col and desc_col != "pulseName":
        column_values[desc_col] = description
        
    if cost_col and cost_col != "pulseName":
        column_values[cost_col] = float(cost_price)
        
    if sell_col and sell_col != "pulseName":
        column_values[sell_col] = float(selling_price)
        
    item_title = name
    if name_col and name_col != "pulseName":
        column_values[name_col] = name
        
    return {
        "item_name": item_title,
        "column_values": column_values
    }

