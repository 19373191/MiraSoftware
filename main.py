"""
M.I.R.A. (Middleware for Integration and Real-time Automation)
FastAPI Main Application Entrypoint & Modular Multi-Page Router.
"""

from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional
try:
    import uvicorn
except ImportError:
    uvicorn = None

try:
    from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Request, Response, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except ImportError:
    # Lightweight fallback for minimal offline test environments
    class FastAPI:
        def __init__(self, *args, **kwargs): pass
        def get(self, *args, **kwargs): return lambda f: f
        def post(self, *args, **kwargs): return lambda f: f
        def add_middleware(self, *args, **kwargs): pass
        def mount(self, *args, **kwargs): pass
        def include_router(self, *args, **kwargs): pass
        def on_event(self, *args, **kwargs): return lambda f: f
    class CORSMiddleware: pass
    class StaticFiles: pass
    class BackgroundTasks: pass
    class Depends:
        def __init__(self, *args, **kwargs): pass
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
        HTTP_200_OK = 200
        HTTP_303_SEE_OTHER = 303
        HTTP_500_INTERNAL_SERVER_ERROR = 500

from config import settings
from connectors.monday_connector import MondayConnector
from connectors.xero_connector import XeroConnector
from engine.transformer import (
    DataTransformer,
    monday_item_to_xero_contact,
    monday_item_to_xero_product,
    monday_items_to_xero_invoice,
    TransformerValidationError,
)
from models.db import init_db, SessionLocal
from models.invoice import InvoiceBatchResponse, InvoiceStatus

from routes.admin_routes import router as admin_router
from routes.auth_routes import router as auth_router, seed_super_admin
from routes.connectors_routes import router as connectors_router
from routes.mappings_routes import router as mappings_router
from routes.sync_routes import router as sync_router

from utils.auth import get_optional_current_user
from utils.logger import logger

app = FastAPI(
    title=settings.app_name,
    description="Lightweight Enterprise Integration Middleware connecting Monday.com, Xero, and automated workflows.",
    version="2.0.4",
    debug=settings.debug,
)

# Enable CORS for cross-system integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Register Feature APIRouters
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(connectors_router)
app.include_router(mappings_router)
app.include_router(sync_router)

transformer = DataTransformer()
monday_connector = MondayConnector()
xero_connector = XeroConnector()


@app.on_event("startup")
def startup_event():
    """Initializes SQLite database schema and seeds super-admin account on startup."""
    try:
        init_db()
        db = SessionLocal()
        try:
            seed_super_admin(db)
            
            # Ensure correct subitem column mappings for Invoice Board 5101138242
            from models.db import FieldMapping
            invoice_board_id = "5101138242"
            
            existing_mappings = db.query(FieldMapping).filter(FieldMapping.board_id == invoice_board_id).all()
            if existing_mappings:
                correct_subitem_mappings = {
                    "Invoice.LineItems[0].ItemCode": "board_relation_mm6jp0r9",
                    "Invoice.LineItems[0].Quantity": "numeric_mm66tnmw",
                    "Invoice.LineItems[0].UnitAmount": "numeric_mm662hn4",
                    "Invoice.LineItems[0].Description": "lookup_mm6j8ck6",
                }
                
                updated = False
                for target_path, correct_col in correct_subitem_mappings.items():
                    mapping_row = next((m for m in existing_mappings if m.target_xero_path == target_path), None)
                    if mapping_row:
                        if mapping_row.source_column != correct_col:
                            logger.info("Migrating mapping target %s on board %s: %s -> %s", 
                                        target_path, invoice_board_id, mapping_row.source_column, correct_col)
                            mapping_row.source_column = correct_col
                            updated = True
                    else:
                        logger.info("Seeding missing subitem mapping %s -> %s on board %s", 
                                    target_path, correct_col, invoice_board_id)
                        new_mapping = FieldMapping(
                            user_id=1,
                            board_id=invoice_board_id,
                            target_xero_path=target_path,
                            source_column=correct_col,
                            custom_override_path="",
                            mapping_version="v2.0.4",
                        )
                        db.add(new_mapping)
                        updated = True
                if updated:
                    db.commit()
        finally:
            if hasattr(db, "close"):
                db.close()
        logger.info("Database initialization and super-admin seeding complete.")
    except Exception as e:
        logger.error("Startup database initialization error: %s", e)


@app.get("/", response_class=RedirectResponse, tags=["General"])
async def root(request: Request):
    """
    Default route redirecting users to /sync if authenticated or /login if unauthenticated.
    """
    try:
        db = SessionLocal()
        try:
            current_user = get_optional_current_user(request, db)
        finally:
            if hasattr(db, "close"):
                db.close()

        if current_user:
            return RedirectResponse(url="/sync", status_code=status.HTTP_303_SEE_OTHER)
    except Exception:
        pass

    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/health", tags=["System Health"])
async def health_check() -> Dict[str, Any]:
    """Health check endpoint for monitoring and uptime probes."""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "environment": settings.app_env,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.4",
    }


@app.post(
    "/api/v1/transform/batch",
    response_model=InvoiceBatchResponse,
    status_code=status.HTTP_200_OK,
    tags=["Transformation Engine"],
)
async def transform_batch(
    records: List[Dict[str, Any]],
    group_by_company: bool = True,
    invoice_status: InvoiceStatus = InvoiceStatus.DRAFT,
) -> InvoiceBatchResponse:
    """Batch transformation endpoint converting flat Monday transaction feeds to Xero invoices."""
    try:
        logger.info("Received batch transformation request with %d records.", len(records))
        if group_by_company:
            result = transformer.transform_batch_grouped(records, status=invoice_status)
        else:
            invoices = []
            failures = []
            for rec in records:
                res = transformer.transform_record_to_invoice(rec, status=invoice_status)
                if res["status"] == "success":
                    invoices.append(res["invoice"])
                else:
                    failures.append(res)
            result = InvoiceBatchResponse(
                status="success" if not failures else "partial_success",
                transformed_invoices=invoices,
                failed_records=failures,
            )
        return result
    except Exception as exc:
        logger.error("Unhandled error during batch transformation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transformation error: {str(exc)}",
        )


def _process_monday_item_changed(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous / Background handler for Monday item changed webhooks."""
    event = payload.get("event", payload)
    item_id = str(
        event.get("pulseId")
        or event.get("itemId")
        or event.get("item_id")
        or payload.get("item_id")
        or payload.get("id")
        or ""
    )
    board_id = str(event.get("boardId") or payload.get("board_id") or settings.monday_board_id or "")

    logger.info("Processing Monday item-changed event for item_id: %s (board_id: %s)", item_id, board_id)

    if not item_id:
        logger.warning("No item ID found in Monday webhook payload: %s", payload)
        return {"status": "ignored", "reason": "Missing item ID"}

    # Fetch custom mappings for this board_id and connected boards from database
    mappings_dict = {}
    customer_mappings = {}
    product_mappings = {}
    db = SessionLocal()
    try:
        from models.db import FieldMapping
        
        # 1. Fetch current board mappings (Invoice board)
        mappings = db.query(FieldMapping).filter(FieldMapping.board_id == board_id).all()
        if not mappings:
            # Fallback to default mappings
            mappings = db.query(FieldMapping).filter(FieldMapping.board_id == "default").all()
        for m in mappings:
            mappings_dict[m.target_xero_path] = m.source_column

        # 2. Fetch customer board mappings
        cust_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Invoice.Contact.EmailAddress").first()
        cust_board_id = cust_mapping.board_id if cust_mapping else "default"
        cust_mappings = db.query(FieldMapping).filter(FieldMapping.board_id == cust_board_id).all()
        for m in cust_mappings:
            customer_mappings[m.target_xero_path] = m.source_column

        # 3. Fetch product board mappings
        prod_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Item.Code").first()
        prod_board_id = prod_mapping.board_id if prod_mapping else "5101138235"
        prod_mappings = db.query(FieldMapping).filter(FieldMapping.board_id == prod_board_id).all()
        for m in prod_mappings:
            product_mappings[m.target_xero_path] = m.source_column

    except Exception as db_err:
        logger.warning("Failed to load mappings for board %s from DB: %s", board_id, db_err)
    finally:
        if hasattr(db, "close"):
            db.close()

    item_data: Optional[Dict[str, Any]] = None
    subitems: List[Dict[str, Any]] = []

    try:
        board_items = monday_connector.query_board_items(board_id=board_id, limit=100)
        for bi in board_items:
            if str(bi.get("id")) == item_id:
                item_data = bi
                subitems = bi.get("subitems", [])
                break
    except Exception as fetch_err:
        logger.error("Failed to query item %s from Monday board: %s", item_id, fetch_err)

    if not item_data:
        item_data = event if isinstance(event, dict) else payload

    try:
        contact_model = monday_item_to_xero_contact(item_data, mappings=mappings_dict, customer_mappings=customer_mappings)
        product_model = None
        try:
            product_model = monday_item_to_xero_product(item_data, mappings=mappings_dict)
        except Exception as prod_err:
            logger.info("Product mapping optional/skipped for item %s: %s", item_id, prod_err)

        invoice_model = monday_items_to_xero_invoice(
            item_data,
            monday_subitems=subitems,
            mappings=mappings_dict,
            customer_mappings=customer_mappings,
            product_mappings=product_mappings,
        )

    except (TransformerValidationError, Exception) as val_err:
        error_log = f"Validation Error: {val_err}"
        logger.error("Transformation failed for Monday item %s: %s", item_id, error_log)

        try:
            monday_connector.update_item_status(item_id, "Sync Error", board_id=board_id)
            monday_connector.post_item_update(item_id, f" M.I.R.A. Sync Error:\n{error_log}")
        except Exception as update_err:
            logger.error("Failed to post error status to Monday for item %s: %s", item_id, update_err)

        return {"status": "error", "item_id": item_id, "error": error_log}

    try:
        logger.info("Pushing Contact '%s' to Xero...", contact_model.Name)
        xero_connector.create_or_update_contact(contact_model)

        if product_model:
            logger.info("Pushing Product '%s' to Xero...", product_model.Code)
            try:
                xero_connector.create_product_item(product_model)
            except Exception as prod_xero_err:
                logger.warning("Xero item push notice (non-fatal): %s", prod_xero_err)

        logger.info("Pushing Invoice for '%s' to Xero...", contact_model.Name)
        xero_response = xero_connector.create_invoice(invoice_model)

        xero_invoices = xero_response.get("Invoices", [])
        invoice_number = (
            xero_invoices[0].get("InvoiceNumber")
            or xero_invoices[0].get("InvoiceID")
            or invoice_model.issue_date
            or "INV-SUCCESS"
        ) if xero_invoices else "INV-SUCCESS"

    except Exception as xero_err:
        error_log = f"Xero Validation Exception: {xero_err}"
        logger.error("Xero submission failed for Monday item %s: %s", item_id, error_log)

        try:
            monday_connector.update_item_status(item_id, "Sync Error", board_id=board_id)
            monday_connector.post_item_update(item_id, f" Xero Submission Error:\n{error_log}")
        except Exception as update_err:
            logger.error("Failed to update error status on Monday item %s: %s", item_id, update_err)

        return {"status": "error", "item_id": item_id, "error": error_log}

    try:
        monday_connector.update_item_status(item_id, "Synced to Xero", board_id=board_id)
        comment_text = (
            f" Successfully synced to Xero!\n"
            f"• Invoice Number: {invoice_number}\n"
            f"• Contact: {contact_model.Name}\n"
            f"• Amount: £{invoice_model.TotalAmount:.2f}"
        )
        monday_connector.post_item_update(item_id, comment_text)
        logger.info("Successfully completed Monday->Xero real-time sync for item %s.", item_id)
    except Exception as post_err:
        logger.error("Failed to post success comment to Monday item %s: %s", item_id, post_err)

    return {
        "status": "success",
        "item_id": item_id,
        "xero_invoice_number": invoice_number,
        "total_amount": invoice_model.TotalAmount,
    }


@app.post("/webhooks/monday/item-changed", tags=["Real-time Webhooks"])
@app.post("/api/v1/webhooks/monday/item-changed", tags=["Real-time Webhooks"])
async def monday_item_changed_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Real-time webhook receiving column change events from Monday.com."""
    logger.info("Received Monday item-changed webhook endpoint call.")
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    return _process_monday_item_changed(payload)


@app.post("/webhooks/xero/invoice-updated", tags=["Real-time Webhooks"])
@app.post("/api/v1/webhooks/xero/invoice-updated", tags=["Real-time Webhooks"])
async def xero_invoice_updated_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Real-time webhook receiving payment & status updates from Xero."""
    logger.info("Received Xero invoice-updated webhook payload.")
    events = payload.get("events", [payload])
    updated_items = []

    for ev in events:
        invoice_id = str(ev.get("resourceId") or ev.get("invoice_id") or ev.get("InvoiceID") or "")
        invoice_status = str(ev.get("status") or ev.get("Status") or "PAID").upper()
        reference = str(ev.get("reference") or ev.get("Reference") or ev.get("monday_item_id") or "")

        logger.info("Processing Xero invoice update (InvoiceID: %s, Status: %s)", invoice_id, invoice_status)

        if invoice_status == "PAID":
            target_item_id = reference or invoice_id
            if target_item_id:
                try:
                    monday_connector.update_item_status(target_item_id, "Paid")
                    monday_connector.post_item_update(
                        target_item_id,
                        f" Xero Payment Received: Invoice {invoice_id} status updated to Paid.",
                    )
                    updated_items.append(target_item_id)
                    logger.info("Updated Monday item %s status to Paid.", target_item_id)
                except Exception as m_err:
                    logger.error("Failed to update Monday status to Paid for %s: %s", target_item_id, m_err)

    return {
        "status": "success",
        "processed_events": len(events),
        "updated_monday_items": updated_items,
    }


if __name__ == "__main__":
    if uvicorn:
        uvicorn.run(
            "main:app",
            host=settings.host,
            port=settings.port,
            reload=settings.debug,
        )

# Trigger uvicorn reload 2026-08-29

