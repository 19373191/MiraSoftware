"""
M.I.R.A. Field Mapping Management Router.

Provides web pages and API endpoints for configuring translation rules between
Monday.com board columns and Xero REST API invoice schemas.
"""

from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.templating import Jinja2Templates
except ImportError:
    class APIRouter:
        def __init__(self, *args, **kwargs): pass
        def __getattr__(self, name): return lambda *a, **k: lambda f: f
    class Depends:
        def __init__(self, *args, **kwargs): pass
    def Form(*args, **kwargs): return None
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str):
            self.status_code = status_code
            self.detail = detail
    class Request: pass
    class Response: pass
    class HTMLResponse: pass
    class RedirectResponse: pass
    class Jinja2Templates:
        def __init__(self, *args, **kwargs): pass
        def TemplateResponse(self, *args, **kwargs): return {}
    class status:
        HTTP_303_SEE_OTHER = 303

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any

from models.db import Credentials, FieldMapping, User, get_db
from utils.auth import get_current_user, require_admin_or_higher
from utils.logger import logger
from connectors.monday_connector import MondayConnector

router = APIRouter(tags=["Schema Mappings"])
templates = Jinja2Templates(directory="templates")

DEFAULT_MAPPING_VERSION = "v2.0.4"

DEFAULT_COLUMN_TITLES: Dict[str, str] = {
    # Customer Board (5101138223) Columns from Monday
    "name": "Name",
    "text_mm663vnh": "Account Number",
    "text_mm66sr6t": "First Name",
    "text_mm668mv4": "Last Name",
    "status": "Status",
    "date4": "Date",
    "text_mm66cg69": "Phone",
    "email_mm5w89d8": "Email",
    "text_mm5wwdh": "Address",
    "text_mm66zhdw": "Xero ID",
    "board_relation_mm67g8vx": "Link to Invoices",
    "text_mm6xnhdy": "Holidays",
    # Invoice Board (5101138242) Columns from Monday
    "numeric_mm6631e9": "Invoice Total",
    "numeric_mm66h8ce": "Invoice Total",
    "delivery_address": "Delivery Address",
    "text_mm66zpe0": "Delivery Address",
    "board_relation_mm67qge0": "Contact",
    "board_relation_mm6jp0r9": "Products (Subitem)",
    "numeric_mm66tnmw": "Quantity (Subitem)",
    "numeric_mm662hn4": "Unit Amount (Subitem)",
    "lookup_mm6j8ck6": "Description (Subitem)",
    # Product Board (5101138235) Columns from Monday
    "item_name": "Item Name",
    "item_number": "Item Code",
    "description": "Description",
    "cost_price": "Cost Price",
    "selling_price": "Selling Price",
    # Legacy / prototype columns
    "transaction_id": "Transaction ID",
    "company_name": "Company Name",
    "customer_first_name": "Customer First Name",
    "customer_last_name": "Customer Last Name",
    "email_address": "Email Address",
    "item_description": "Item Description",
    "quantity": "Quantity",
    "unit_price": "Unit Price",
}


def get_column_title(column_id: str, board_columns: Optional[List[Dict[str, Any]]] = None) -> str:
    """Returns a user-friendly display title for a Monday.com column identifier."""
    if not column_id:
        return ""
    if board_columns:
        for col in board_columns:
            if col.get("id") == column_id and col.get("title") and col.get("title") != column_id:
                return col["title"]
    if column_id in DEFAULT_COLUMN_TITLES:
        return DEFAULT_COLUMN_TITLES[column_id]
    cleaned = column_id.replace("_", " ").strip()
    return cleaned.title() if cleaned else column_id


DEFAULT_CUSTOMER_MAPPINGS = [
    {
        "source_column": "name",
        "target_xero_path": "Invoice.Contact.Name",
        "custom_override_path": "Contact.Name",
    },
    {
        "source_column": "text_mm663vnh",
        "target_xero_path": "Invoice.Contact.AccountNumber",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm66sr6t",
        "target_xero_path": "Invoice.Contact.FirstName",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm668mv4",
        "target_xero_path": "Invoice.Contact.LastName",
        "custom_override_path": "",
    },
    {
        "source_column": "email_mm5w89d8",
        "target_xero_path": "Invoice.Contact.EmailAddress",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm66cg69",
        "target_xero_path": "Invoice.Contact.Phone",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm5wwdh",
        "target_xero_path": "Invoice.Contact.Address",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm66zhdw",
        "target_xero_path": "Invoice.Contact.ContactID",
        "custom_override_path": "",
    },
]


DEFAULT_MAPPINGS = [
    {
        "source_column": "transaction_id",
        "target_xero_path": "Invoice.Reference",
        "custom_override_path": "TX-{transaction_id}",
    },
    {
        "source_column": "company_name",
        "target_xero_path": "Invoice.Contact.Name",
        "custom_override_path": "Contact.Name",
    },
    {
        "source_column": "customer_first_name",
        "target_xero_path": "Invoice.Contact.FirstName",
        "custom_override_path": "Contact.FirstName",
    },
    {
        "source_column": "customer_last_name",
        "target_xero_path": "Invoice.Contact.LastName",
        "custom_override_path": "Contact.LastName",
    },
    {
        "source_column": "email_address",
        "target_xero_path": "Invoice.Contact.EmailAddress",
        "custom_override_path": "Contact.EmailAddress",
    },
    {
        "source_column": "item_description",
        "target_xero_path": "Invoice.LineItems[0].Description",
        "custom_override_path": "LineItems.Description",
    },
    {
        "source_column": "quantity",
        "target_xero_path": "Invoice.LineItems[0].Quantity",
        "custom_override_path": "LineItems.Quantity",
    },
    {
        "source_column": "unit_price",
        "target_xero_path": "Invoice.LineItems[0].UnitAmount",
        "custom_override_path": "LineItems.UnitAmount",
    },
]

DEFAULT_INVOICE_MAPPINGS = [
    {
        "source_column": "name",
        "target_xero_path": "Invoice.InvoiceNumber",
        "custom_override_path": "",
    },
    {
        "source_column": "status",
        "target_xero_path": "Invoice.Status",
        "custom_override_path": "",
    },
    {
        "source_column": "date4",
        "target_xero_path": "Invoice.Date",
        "custom_override_path": "",
    },
    {
        "source_column": "numeric_mm66h8ce",
        "target_xero_path": "Invoice.Total",
        "custom_override_path": "",
    },
    {
        "source_column": "text_mm66zpe0",
        "target_xero_path": "Invoice.Contact.Address",
        "custom_override_path": "",
    },
    {
        "source_column": "board_relation_mm67qge0",
        "target_xero_path": "Invoice.Contact.Name",
        "custom_override_path": "",
    },
    # Subitems mappings
    {
        "source_column": "board_relation_mm6jp0r9",
        "target_xero_path": "Invoice.LineItems[0].ItemCode",
        "custom_override_path": "",
    },
    {
        "source_column": "numeric_mm66tnmw",
        "target_xero_path": "Invoice.LineItems[0].Quantity",
        "custom_override_path": "",
    },
    {
        "source_column": "numeric_mm662hn4",
        "target_xero_path": "Invoice.LineItems[0].UnitAmount",
        "custom_override_path": "",
    },
    {
        "source_column": "lookup_mm6j8ck6",
        "target_xero_path": "Invoice.LineItems[0].Description",
        "custom_override_path": "",
    },
]


DEFAULT_PRODUCT_MAPPINGS = [
    {
        "source_column": "item_name",
        "target_xero_path": "Item.Name",
        "custom_override_path": "",
    },
    {
        "source_column": "item_number",
        "target_xero_path": "Item.Code",
        "custom_override_path": "",
    },
    {
        "source_column": "description",
        "target_xero_path": "Item.Description",
        "custom_override_path": "",
    },
    {
        "source_column": "cost_price",
        "target_xero_path": "Item.PurchaseDetails.UnitPrice",
        "custom_override_path": "",
    },
    {
        "source_column": "selling_price",
        "target_xero_path": "Item.SalesDetails.UnitPrice",
        "custom_override_path": "",
    },
]


DEFAULT_CUSTOMER_COLUMNS = [
    {"id": "name", "title": "Name", "type": "text"},
    {"id": "text_mm663vnh", "title": "Account Number", "type": "text"},
    {"id": "text_mm66sr6t", "title": "First Name", "type": "text"},
    {"id": "text_mm668mv4", "title": "Last Name", "type": "text"},
    {"id": "status", "title": "Status", "type": "color"},
    {"id": "date4", "title": "Date", "type": "date"},
    {"id": "text_mm66cg69", "title": "Phone", "type": "text"},
    {"id": "email_mm5w89d8", "title": "Email", "type": "email"},
    {"id": "text_mm5wwdh", "title": "Address", "type": "text"},
    {"id": "text_mm66zhdw", "title": "Xero ID", "type": "text"},
    {"id": "board_relation_mm67g8vx", "title": "Link to Invoices", "type": "board-relation"},
    {"id": "text_mm6xnhdy", "title": "Holidays", "type": "text"},
]


def ensure_user_mappings(db: Session, user_id: int, board_id: Optional[str] = "default", is_invoice: bool = False, is_product: bool = False, is_customer: bool = False) -> List[FieldMapping]:
    """Ensures a user has active field mappings for a specific board, seeding defaults if empty."""
    if not db:
        return []

    target_board_id = board_id or ("5101138242" if is_invoice else ("5101138235" if is_product else ("5101138223" if is_customer else "default")))
    mappings = (
        db.query(FieldMapping)
        .filter(FieldMapping.user_id == user_id, FieldMapping.board_id == target_board_id)
        .order_by(FieldMapping.id.asc())
        .all()
    )

    if not mappings:
        if is_invoice:
            defaults = DEFAULT_INVOICE_MAPPINGS
        elif is_product:
            defaults = DEFAULT_PRODUCT_MAPPINGS
        elif is_customer or target_board_id in ("5101138223", "default_customer"):
            defaults = DEFAULT_CUSTOMER_MAPPINGS
        else:
            defaults = DEFAULT_MAPPINGS
            
        for item in defaults:
            fm = FieldMapping(
                user_id=user_id,
                source_column=item["source_column"],
                target_xero_path=item["target_xero_path"],
                custom_override_path=item.get("custom_override_path", ""),
                mapping_version=DEFAULT_MAPPING_VERSION,
                board_id=target_board_id,
            )
            db.add(fm)
        db.commit()

        mappings = (
            db.query(FieldMapping)
            .filter(FieldMapping.user_id == user_id, FieldMapping.board_id == target_board_id)
            .order_by(FieldMapping.id.asc())
            .all()
        )

    return mappings



@router.get("/mappings", response_class=HTMLResponse)
async def mappings_page(
    request: Request,
    board_id_1: Optional[str] = None,
    board_id_2: Optional[str] = None,
    board_id_3: Optional[str] = None,
    error: Optional[str] = None,
    success: Optional[str] = None,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Renders the Schema Mapping Management page displaying active translation rules.
    """
    user_id = getattr(current_user, "id", 1)
    logger.info("mappings_page request.query_params: %s", dict(request.query_params))
    logger.info("mappings_page args: board_id_1=%s, board_id_2=%s, board_id_3=%s", board_id_1, board_id_2, board_id_3)

    # Load Monday Credentials to default board_id_1
    monday_cred = db.query(Credentials).filter(
        Credentials.user_id == user_id,
        Credentials.platform_name == "monday"
    ).first() if db else None

    # Retrieve parameters or defaults
    query_params = dict(request.query_params)
    board_id_1 = query_params.get("board_id_1") or board_id_1
    board_id_2 = query_params.get("board_id_2") or board_id_2
    board_id_3 = query_params.get("board_id_3") or board_id_3

    if not board_id_1:
        if monday_cred and monday_cred.board_id:
            board_id_1 = monday_cred.board_id
        else:
            board_id_1 = "5101138223"

    if not board_id_2:
        # Try to find existing mapping for Invoice to recover board_id_2
        stored_inv = db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.target_xero_path.like("Invoice.InvoiceNumber%")
        ).first()
        if stored_inv and stored_inv.board_id:
            board_id_2 = stored_inv.board_id
        else:
            board_id_2 = "5101138242"

    if not board_id_3:
        board_id_3 = "5101138235"

    # Fetch boards list from Monday for dropdowns
    boards = []
    if monday_cred and monday_cred.api_key:
        try:
            connector = MondayConnector(api_key=monday_cred.api_key)
            boards = connector.query_boards()
        except Exception as e:
            logger.warning("Could not fetch boards from Monday: %s", e)

    # Ensure mappings exist for all boards
    ensure_user_mappings(db, user_id, board_id_1, is_customer=True)
    ensure_user_mappings(db, user_id, board_id_2, is_invoice=True)
    ensure_user_mappings(db, user_id, board_id_3, is_product=True)
    
    # Fetch mappings for board ID from DB
    mappings_b1 = db.query(FieldMapping).filter(FieldMapping.user_id == user_id, FieldMapping.board_id == board_id_1).all()
    mappings_b2 = db.query(FieldMapping).filter(FieldMapping.user_id == user_id, FieldMapping.board_id == board_id_2).all()
    mappings_b3 = db.query(FieldMapping).filter(FieldMapping.user_id == user_id, FieldMapping.board_id == board_id_3).all()

    # Construct the reversed rows for UI representation
    # Xero target path is the Source, and Monday board columns are the Targets.
    rows = []
    seen_xero_paths = set()
    base_mappings_1 = DEFAULT_CUSTOMER_MAPPINGS if board_id_1 in ("5101138223", "default", "default_customer") else DEFAULT_CUSTOMER_MAPPINGS
    for item in base_mappings_1:
        xero_path = item["target_xero_path"]
        seen_xero_paths.add(xero_path)
        m1 = next((m for m in mappings_b1 if m.target_xero_path == xero_path), None)
        col_val = m1.source_column if m1 else item["source_column"]
        rows.append({
            "target_xero_path": xero_path,
            "board_1_col": col_val,
            "board_1_col_title": get_column_title(col_val),
            "custom_override_path": m1.custom_override_path if m1 else item.get("custom_override_path", ""),
        })
    for m in mappings_b1:
        if m.target_xero_path not in seen_xero_paths:
            seen_xero_paths.add(m.target_xero_path)
            rows.append({
                "target_xero_path": m.target_xero_path,
                "board_1_col": m.source_column,
                "board_1_col_title": get_column_title(m.source_column),
                "custom_override_path": m.custom_override_path or "",
            })

    rows_b2 = []
    for item in DEFAULT_INVOICE_MAPPINGS:
        xero_path = item["target_xero_path"]
        m2 = next((m for m in mappings_b2 if m.target_xero_path == xero_path), None)
        col_val = m2.source_column if m2 else item["source_column"]
        rows_b2.append({
            "target_xero_path": xero_path,
            "board_2_col": col_val,
            "board_2_col_title": get_column_title(col_val),
            "custom_override_path": m2.custom_override_path if m2 else item.get("custom_override_path", ""),
        })

    rows_b3 = []
    for item in DEFAULT_PRODUCT_MAPPINGS:
        xero_path = item["target_xero_path"]
        m3 = next((m for m in mappings_b3 if m.target_xero_path == xero_path), None)
        col_val = m3.source_column if m3 else item["source_column"]
        rows_b3.append({
            "target_xero_path": xero_path,
            "board_3_col": col_val,
            "board_3_col_title": get_column_title(col_val),
            "custom_override_path": m3.custom_override_path if m3 else item.get("custom_override_path", ""),
        })

    return templates.TemplateResponse(
        request,
        "mappings.html",
        {
            "current_user": current_user,
            "mapping_rows": rows,
            "mapping_rows_b2": rows_b2,
            "mapping_rows_b3": rows_b3,
            "boards": boards,
            "mapping_version": DEFAULT_MAPPING_VERSION,
            "board_id_1": board_id_1,
            "board_id_2": board_id_2,
            "board_id_3": board_id_3,
            "error": error,
            "success": success,
            "hide_navbar": True,
        },
    )



@router.post("/api/mappings/save")
async def save_mappings(
    request: Request,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Saves updated field mapping rules to database for Board 1, Board 2 and Board 3.
    """
    user_id = getattr(current_user, "id", 1)
    
    # Parse form parameters
    form_data = await request.form() if hasattr(request, "form") else {}
    
    board_id_1 = str(form_data.get("board_id_1", "")).strip() or "default"
    board_id_2 = str(form_data.get("board_id_2", "")).strip() or "5101138242"
    board_id_3 = str(form_data.get("board_id_3", "")).strip() or "5101138235"
    
    # Read lists for Board 1
    xero_paths_1 = form_data.getlist("target_xero_path_1")
    board_1_cols = form_data.getlist("board_1_col_1")
    overrides_1 = form_data.getlist("custom_override_path_1")
    
    # Fallback to standard field names if not structured by target board suffix
    if not xero_paths_1 and form_data.getlist("target_xero_path"):
        xero_paths_1 = form_data.getlist("target_xero_path")
        board_1_cols = form_data.getlist("board_1_col")
        overrides_1 = form_data.getlist("custom_override_path")
        
    # Read lists for Board 2
    xero_paths_2 = form_data.getlist("target_xero_path_2")
    board_2_cols = form_data.getlist("board_2_col_2")
    overrides_2 = form_data.getlist("custom_override_path_2")
    
    # Read lists for Board 3 (Products)
    xero_paths_3 = form_data.getlist("target_xero_path_3")
    board_3_cols = form_data.getlist("board_3_col_3")
    overrides_3 = form_data.getlist("custom_override_path_3")
    
    if db:
        # Delete old mappings for board ID 1
        db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.board_id == board_id_1
        ).delete(synchronize_session=False)

        # Save Board 1 Mappings
        for idx in range(len(xero_paths_1)):
            xero_path = xero_paths_1[idx].strip()
            if not xero_path:
                continue
            override = overrides_1[idx].strip() if idx < len(overrides_1) else ""
            b1_col = board_1_cols[idx].strip() if idx < len(board_1_cols) else ""
            if b1_col:
                fm1 = FieldMapping(
                    user_id=user_id,
                    board_id=board_id_1,
                    target_xero_path=xero_path,
                    source_column=b1_col,
                    custom_override_path=override or None,
                    mapping_version=DEFAULT_MAPPING_VERSION,
                )
                db.add(fm1)

        # Delete old mappings for board ID 2
        db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.board_id == board_id_2
        ).delete(synchronize_session=False)

        # Save Board 2 Mappings
        for idx in range(len(xero_paths_2)):
            xero_path = xero_paths_2[idx].strip()
            if not xero_path:
                continue
            override = overrides_2[idx].strip() if idx < len(overrides_2) else ""
            b2_col = board_2_cols[idx].strip() if idx < len(board_2_cols) else ""
            if b2_col:
                fm2 = FieldMapping(
                    user_id=user_id,
                    board_id=board_id_2,
                    target_xero_path=xero_path,
                    source_column=b2_col,
                    custom_override_path=override or None,
                    mapping_version=DEFAULT_MAPPING_VERSION,
                )
                db.add(fm2)

        # Delete old mappings for board ID 3
        db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.board_id == board_id_3
        ).delete(synchronize_session=False)

        # Save Board 3 Mappings
        for idx in range(len(xero_paths_3)):
            xero_path = xero_paths_3[idx].strip()
            if not xero_path:
                continue
            override = overrides_3[idx].strip() if idx < len(overrides_3) else ""
            b3_col = board_3_cols[idx].strip() if idx < len(board_3_cols) else ""
            if b3_col:
                fm3 = FieldMapping(
                    user_id=user_id,
                    board_id=board_id_3,
                    target_xero_path=xero_path,
                    source_column=b3_col,
                    custom_override_path=override or None,
                    mapping_version=DEFAULT_MAPPING_VERSION,
                )
                db.add(fm3)

        db.commit()

    logger.info("Saved board mappings for user %s (board1: %s, board2: %s, board3: %s)", getattr(current_user, "email", "user"), board_id_1, board_id_2, board_id_3)

    return RedirectResponse(
        url=f"/mappings?board_id_1={board_id_1}&board_id_2={board_id_2}&board_id_3={board_id_3}&success=Saved+schema+mappings+successfully.",
        status_code=status.HTTP_303_SEE_OTHER,
    )



@router.post("/api/mappings/reset")
async def reset_mappings_to_default(
    request: Request,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Restores default mappings for Board 1, Board 2, Board 3 or all.
    """
    user_id = getattr(current_user, "id", 1)
    form_data = await request.form() if hasattr(request, "form") else {}
    
    board_id_1 = str(form_data.get("board_id_1", "")).strip() or "default"
    board_id_2 = str(form_data.get("board_id_2", "")).strip() or "5101138242"
    board_id_3 = str(form_data.get("board_id_3", "")).strip() or "5101138235"
    reset_target = str(form_data.get("reset_target", "both")).strip()

    if db:
        if reset_target in ("both", "1", "all"):
            db.query(FieldMapping).filter(
                FieldMapping.user_id == user_id,
                FieldMapping.board_id == board_id_1
            ).delete(synchronize_session=False)
            
            # Seed default mappings for Board 1
            defaults_1 = DEFAULT_CUSTOMER_MAPPINGS if board_id_1 in ("5101138223", "default", "default_customer") else DEFAULT_CUSTOMER_MAPPINGS
            for item in defaults_1:
                fm = FieldMapping(
                    user_id=user_id,
                    source_column=item["source_column"],
                    target_xero_path=item["target_xero_path"],
                    custom_override_path=item.get("custom_override_path", ""),
                    mapping_version=DEFAULT_MAPPING_VERSION,
                    board_id=board_id_1,
                )
                db.add(fm)

        if reset_target in ("both", "2", "all"):
            db.query(FieldMapping).filter(
                FieldMapping.user_id == user_id,
                FieldMapping.board_id == board_id_2
            ).delete(synchronize_session=False)
            
            # Seed default mappings for Board 2
            for item in DEFAULT_INVOICE_MAPPINGS:
                fm = FieldMapping(
                    user_id=user_id,
                    source_column=item["source_column"],
                    target_xero_path=item["target_xero_path"],
                    custom_override_path=item.get("custom_override_path", ""),
                    mapping_version=DEFAULT_MAPPING_VERSION,
                    board_id=board_id_2,
                )
                db.add(fm)

        if reset_target in ("3", "all"):
            db.query(FieldMapping).filter(
                FieldMapping.user_id == user_id,
                FieldMapping.board_id == board_id_3
            ).delete(synchronize_session=False)
            
            # Seed default mappings for Board 3
            for item in DEFAULT_PRODUCT_MAPPINGS:
                fm = FieldMapping(
                    user_id=user_id,
                    source_column=item["source_column"],
                    target_xero_path=item["target_xero_path"],
                    custom_override_path=item.get("custom_override_path", ""),
                    mapping_version=DEFAULT_MAPPING_VERSION,
                    board_id=board_id_3,
                )
                db.add(fm)
        db.commit()

    return RedirectResponse(
        url=f"/mappings?board_id_1={board_id_1}&board_id_2={board_id_2}&board_id_3={board_id_3}&success=Default+mappings+restored.",
        status_code=status.HTTP_303_SEE_OTHER,
    )



@router.post("/api/mappings/delete/{mapping_id}")
async def delete_mapping_row(
    mapping_id: int,
    request: Request,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """Deletes a specific mapping row."""
    user_id = getattr(current_user, "id", 1)
    if db:
        fm = db.query(FieldMapping).filter(FieldMapping.id == mapping_id, FieldMapping.user_id == user_id).first()
        if fm:
            db.delete(fm)
            db.commit()

    return RedirectResponse(
        url="/mappings?success=Mapping+row+deleted.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/api/config")
async def get_config(
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Returns active board configurations and mappings for the current user.
    """
    user_id = getattr(current_user, "id", 1)
    
    # Check credentials for Monday to see default board
    monday_cred = db.query(Credentials).filter(
        Credentials.user_id == user_id,
        Credentials.platform_name == "monday"
    ).first() if db else None

    # Retrieve board IDs
    active_boards = []
    if db:
        res = db.query(FieldMapping.board_id).filter(FieldMapping.user_id == user_id).distinct().all()
        active_boards = [r[0] for r in res if r[0] and r[0] != "default"]

    board_id_1 = active_boards[0] if len(active_boards) > 0 else (monday_cred.board_id if monday_cred else "default")

    # Ensure mappings exist for board 1
    ensure_user_mappings(db, user_id, board_id_1)

    mappings_b1 = db.query(FieldMapping).filter(FieldMapping.user_id == user_id, FieldMapping.board_id == board_id_1).all()

    rows = []
    for item in DEFAULT_MAPPINGS:
        xero_path = item["target_xero_path"]
        
        m1 = next((m for m in mappings_b1 if m.target_xero_path == xero_path), None)
        
        rows.append({
            "target_xero_path": xero_path,
            "board_1_col": m1.source_column if m1 else item["source_column"],
            "custom_override_path": m1.custom_override_path if m1 else item.get("custom_override_path", ""),
        })

    return {
        "board_id_1": board_id_1,
        "mappings": rows,
    }


@router.post("/api/config")
async def save_config(
    payload: dict,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Saves board mappings from SPA dashboard payload.
    """
    user_id = getattr(current_user, "id", 1)
    
    board_id_1 = str(payload.get("board_id_1", "")).strip() or "default"
    
    mappings_list = payload.get("mappings", [])
    
    if db:
        db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.board_id == board_id_1
        ).delete(synchronize_session=False)
        db.commit()

        for m in mappings_list:
            xero_path = str(m.get("target_xero_path", "")).strip()
            if not xero_path:
                continue
                
            override = str(m.get("custom_override_path", "")).strip()
            
            # Save for Board 1
            b1_col = str(m.get("board_1_col", "")).strip()
            if b1_col:
                fm1 = FieldMapping(
                    user_id=user_id,
                    board_id=board_id_1,
                    target_xero_path=xero_path,
                    source_column=b1_col,
                    custom_override_path=override or None,
                    mapping_version=DEFAULT_MAPPING_VERSION,
                )
                db.add(fm1)
                    
        db.commit()

    return {"status": "success"}


@router.get("/api/mappings/board/{board_id}/columns")
async def get_board_columns(
    board_id: str,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Returns column details for a specified Monday.com board ID.
    If Monday is not connected or fails, falls back to default mapping column keys.
    """
    user_id = getattr(current_user, "id", 1)
    
    # Import MondayConnector inside helper to avoid circular dependencies
    from connectors.monday_connector import MondayConnector
    
    monday_cred = db.query(Credentials).filter(
        Credentials.user_id == user_id,
        Credentials.platform_name == "monday"
    ).first() if db else None

    columns = []
    if monday_cred and monday_cred.api_key and board_id and board_id != "default":
        try:
            connector = MondayConnector(api_key=monday_cred.api_key)
            columns = connector.query_board_columns(board_id)
            
            # Check for subtasks column to fetch subitem board columns
            import json
            subitem_board_id = None
            for col in columns:
                if col.get("type") == "subtasks" and col.get("settings_str"):
                    try:
                        settings = json.loads(col["settings_str"])
                        board_ids = settings.get("boardIds")
                        if board_ids and isinstance(board_ids, list):
                            subitem_board_id = str(board_ids[0])
                            break
                    except Exception as e:
                        logger.warning("Could not parse subtasks settings_str: %s", e)
            
            if subitem_board_id:
                try:
                    sub_columns = connector.query_board_columns(subitem_board_id)
                    for scol in sub_columns:
                        scol["title"] = f"{scol['title']} (Subitem)"
                        columns.append(scol)
                except Exception as e:
                    logger.warning("Could not query subitem board columns for board %s: %s", subitem_board_id, e)
        except Exception as e:
            logger.warning("Could not query board columns for board %s: %s", board_id, e)

    # If no columns retrieved, or if default board, return default columns structure
    if not columns:
        if board_id == "5101138235":
            columns = [
                {"id": "item_name", "title": "Item Name", "type": "text"},
                {"id": "item_number", "title": "Item Code", "type": "text"},
                {"id": "description", "title": "Description", "type": "text"},
                {"id": "cost_price", "title": "Cost Price", "type": "numeric"},
                {"id": "selling_price", "title": "Selling Price", "type": "numeric"},
            ]
        elif board_id in ("5101138242", "default_invoice"):
            columns = [
                {"id": "name", "title": "Invoice Number", "type": "text"},
                {"id": "status", "title": "Status", "type": "text"},
                {"id": "date4", "title": "Invoice Date", "type": "date"},
                {"id": "numeric_mm66h8ce", "title": "Invoice Total", "type": "numeric"},
                {"id": "text_mm66zpe0", "title": "Delivery Address", "type": "text"},
                {"id": "board_relation_mm67qge0", "title": "Contact", "type": "board_relation"},
                # Subitem columns fallback
                {"id": "board_relation_mm6jp0r9", "title": "Products (Subitem)", "type": "board_relation"},
                {"id": "numeric_mm66tnmw", "title": "Quantity (Subitem)", "type": "numeric"},
                {"id": "numeric_mm662hn4", "title": "Amount (Subitem)", "type": "numeric"},
                {"id": "lookup_mm6j8ck6", "title": "Description (Subitem)", "type": "text"},
            ]
        else:
            columns = [dict(c) for c in DEFAULT_CUSTOMER_COLUMNS]

    for col in columns:
        if not col.get("title") or col.get("title") == col.get("id"):
            col["title"] = get_column_title(col.get("id", ""))
        
    return {"columns": columns}


@router.get("/api/mappings/board/{board_id}/details")
async def get_board_mapping_details(
    board_id: str,
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db),
):
    """
    Returns column details and saved mappings for a specified Monday.com board ID.
    If Monday is not connected, returns default columns and default mappings.
    """
    user_id = getattr(current_user, "id", 1)
    
    from connectors.monday_connector import MondayConnector
    
    monday_cred = db.query(Credentials).filter(
        Credentials.user_id == user_id,
        Credentials.platform_name == "monday"
    ).first() if db else None

    # Fetch columns from Monday
    columns = []
    if monday_cred and monday_cred.api_key and board_id and board_id != "default":
        try:
            connector = MondayConnector(api_key=monday_cred.api_key)
            columns = connector.query_board_columns(board_id)
            
            # Check for subtasks column to fetch subitem board columns
            import json
            subitem_board_id = None
            for col in columns:
                if col.get("type") == "subtasks" and col.get("settings_str"):
                    try:
                        settings = json.loads(col["settings_str"])
                        board_ids = settings.get("boardIds")
                        if board_ids and isinstance(board_ids, list):
                            subitem_board_id = str(board_ids[0])
                            break
                    except Exception as e:
                        logger.warning("Could not parse subtasks settings_str: %s", e)
            
            if subitem_board_id:
                try:
                    sub_columns = connector.query_board_columns(subitem_board_id)
                    for scol in sub_columns:
                        scol["title"] = f"{scol['title']} (Subitem)"
                        columns.append(scol)
                except Exception as e:
                    logger.warning("Could not query subitem board columns for board %s: %s", subitem_board_id, e)
        except Exception as e:
            logger.warning("Could not query board columns for board %s: %s", board_id, e)

    # Fetch saved mappings for this board
    saved_mappings = []
    if db:
        saved_mappings = db.query(FieldMapping).filter(
            FieldMapping.user_id == user_id,
            FieldMapping.board_id == board_id
        ).all()

    # Fallback to defaults if no columns retrieved
    if not columns:
        if board_id == "5101138235":
            columns = [
                {"id": "item_name", "title": "Item Name", "type": "text"},
                {"id": "item_number", "title": "Item Code", "type": "text"},
                {"id": "description", "title": "Description", "type": "text"},
                {"id": "cost_price", "title": "Cost Price", "type": "numeric"},
                {"id": "selling_price", "title": "Selling Price", "type": "numeric"},
            ]
        elif board_id in ("5101138242", "default_invoice"):
            columns = [
                {"id": "name", "title": "Invoice Number", "type": "text"},
                {"id": "status", "title": "Status", "type": "text"},
                {"id": "date4", "title": "Invoice Date", "type": "date"},
                {"id": "numeric_mm66h8ce", "title": "Invoice Total", "type": "numeric"},
                {"id": "text_mm66zpe0", "title": "Delivery Address", "type": "text"},
                {"id": "board_relation_mm67qge0", "title": "Contact", "type": "board_relation"},
                # Subitem columns fallback
                {"id": "board_relation_mm6jp0r9", "title": "Products (Subitem)", "type": "board_relation"},
                {"id": "numeric_mm66tnmw", "title": "Quantity (Subitem)", "type": "numeric"},
                {"id": "numeric_mm662hn4", "title": "Amount (Subitem)", "type": "numeric"},
                {"id": "lookup_mm6j8ck6", "title": "Description (Subitem)", "type": "text"},
            ]
        else:
            columns = [dict(c) for c in DEFAULT_CUSTOMER_COLUMNS]

    for col in columns:
        if not col.get("title") or col.get("title") == col.get("id"):
            col["title"] = get_column_title(col.get("id", ""))
        
    # Convert saved mappings to list of dicts
    mappings_list = []
    for m in saved_mappings:
        mappings_list.append({
            "target_xero_path": m.target_xero_path,
            "source_column": m.source_column,
            "custom_override_path": m.custom_override_path or "",
        })

    # If no mappings saved yet, return defaults
    if not mappings_list:
        if board_id == "5101138235":
            for item in DEFAULT_PRODUCT_MAPPINGS:
                mappings_list.append({
                    "target_xero_path": item["target_xero_path"],
                    "source_column": item["source_column"],
                    "custom_override_path": item.get("custom_override_path", ""),
                })
        elif board_id in ("5101138242", "default_invoice"):
            for item in DEFAULT_INVOICE_MAPPINGS:
                mappings_list.append({
                    "target_xero_path": item["target_xero_path"],
                    "source_column": item["source_column"],
                    "custom_override_path": item.get("custom_override_path", ""),
                })
        else:
            for item in DEFAULT_CUSTOMER_MAPPINGS:
                mappings_list.append({
                    "target_xero_path": item["target_xero_path"],
                    "source_column": item["source_column"],
                    "custom_override_path": item.get("custom_override_path", ""),
                })

    return {
        "columns": columns,
        "mappings": mappings_list
    }




