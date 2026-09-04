"""
M.I.R.A. Synchronization Engine & Real-Time SSE Event Stream Router.

Executes batch integrations between Monday.com and Xero, streams live log progress
via Server-Sent Events (SSE), and records audit trails in the SyncLog database table.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Generator, List, Optional
from config import settings

try:
    from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, Response, status
    from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
    from fastapi.templating import Jinja2Templates
except ImportError:
    class APIRouter:
        def __init__(self, *args, **kwargs): pass
        def __getattr__(self, name): return lambda *a, **k: lambda f: f
    class BackgroundTasks:
        def add_task(self, *args, **kwargs): pass
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
    class StreamingResponse:
        def __init__(self, content, media_type=None, *args, **kwargs):
            self.content = content
            self.media_type = media_type
    class Jinja2Templates:
        def __init__(self, *args, **kwargs): pass
        def TemplateResponse(self, *args, **kwargs): return {}
    class status:
        HTTP_200_OK = 200
        HTTP_303_SEE_OTHER = 303

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any

from engine.transformer import DataTransformer
from models.db import FieldMapping, SyncLog, User, get_db
from routes.connectors import get_user_credentials
from utils.auth import get_current_user, require_admin_or_higher
from utils.logger import logger

router = APIRouter(tags=["Sync Engine"])
templates = Jinja2Templates(directory="templates")


@router.get("/sync", response_class=HTMLResponse)
async def sync_control_panel(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Renders the Synchronization Control Panel displaying operational settings,
    live processing terminal, and recent SyncLog database audits.
    """
    recent_logs = []
    if db:
        try:
            recent_logs = (
                db.query(SyncLog)
                .order_by(SyncLog.timestamp.desc())
                .limit(10)
                .all()
            )
        except Exception:
            recent_logs = []

    return templates.TemplateResponse(
        request,
        "sync.html",
        {
            "current_user": current_user,
            "recent_logs": recent_logs,
            "error": error,
            "success": success,
        },
    )


import re
from datetime import timedelta

def parse_xero_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        # Microsoft JSON date format: /Date(1691954314000+0000)/
        match = re.search(r'/Date\((\d+)([+-]\d+)?\)/', date_str)
        if match:
            milliseconds = int(match.group(1))
            return datetime.fromtimestamp(milliseconds / 1000.0, timezone.utc)
        
        # ISO format: e.g. 2026-08-13T22:05:14 or 2026-08-13T22:05:14.123456Z
        clean_str = date_str.rstrip('Z')
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def generate_mock_payloads(count: int = 500) -> List[Dict[str, Any]]:
    """Generates synthetic transactions for simulation."""
    try:
        from faker import Faker
        fake = Faker()
        companies = [fake.company() for _ in range(15)]
    except ImportError:
        fake = None
        companies = ["Acme Corp", "Cyberdyne Systems", "Stark Industries", "Wayne Enterprises", "Umbrella Corp"]

    now_utc = datetime.now(timezone.utc)
    payloads = []
    for i in range(count):
        comp = companies[i % len(companies)]
        payloads.append(
            {
                "transaction_id": f"TX-MIRA-{1000 + i}",
                "company_name": comp,
                "email_address": f"billing@{comp.lower().replace(' ', '').replace(',', '')}.com",
                "customer_first_name": "Account",
                "customer_last_name": "Payable",
                "item_description": f"Integration Service License Plan #{ (i % 5) + 1 }",
                "quantity": (i % 4) + 1,
                "unit_price": float((i % 10 + 1) * 150.0),
                "issue_date": "2026-07-20",
                "due_date": "2026-08-20",
                "updated_at": (now_utc - timedelta(minutes=i * 10)).isoformat() + "Z"
            }
        )
    return payloads


@router.get("/api/sync/stream")
async def sync_live_stream(
    request: Request,
    direction: str = "xero_to_monday",
    board_id_1: Optional[str] = None,
    board_id_3: Optional[str] = None,
    batch_count: int = 500,
    group_by_company: bool = True,
    target_account: str = "200",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Server-Sent Events (SSE) streaming endpoint executing batch synchronization
    and emitting real-time progress logs and performance metrics.
    """
    def _event_generator_run(db) -> Generator[str, None, None]:

        start_time = time.time()
        user_id = getattr(current_user, "id", 1)

        # Lookup last successful sync log for the current direction
        last_sync = None
        cutoff_time = None
        if db:
            try:
                last_sync = (
                    db.query(SyncLog)
                    .filter(
                        SyncLog.direction == direction,
                        SyncLog.status.in_(["SUCCESS", "PARTIAL_SUCCESS"])
                    )
                    .order_by(SyncLog.timestamp.desc())
                    .first()
                )
            except Exception as e:
                logger.warning("Could not fetch last SyncLog: %s", e)

        if last_sync and last_sync.timestamp:
            cutoff_time = last_sync.timestamp
            if cutoff_time.tzinfo is None:
                cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

        # Fetch credentials and dry-run flag for both sync directions
        from models.db import Credentials
        xero_cred = get_user_credentials(db, user_id, "xero")
        monday_cred = get_user_credentials(db, user_id, "monday")
        is_dry_run = (os.getenv("XERO_DRY_RUN") == "True") or (not xero_cred or not xero_cred.access_token)

        if direction == "xero_to_monday":
            yield f"data: {json.dumps({'percent': 5, 'log': ' Initializing Xero to Monday.com Synchronization...', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            if cutoff_time:
                log_msg = f" Last successful sync was at {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC. Filtering new/modified data."
                yield f"data: {json.dumps({'percent': 8, 'log': log_msg, 'status': 'IN_PROGRESS'})}\n\n"
            else:
                yield f"data: {json.dumps({'percent': 8, 'log': ' No prior successful sync found for this direction. Processing all records.', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            target_boards = [board_id_1.strip()] if board_id_1 and board_id_1.strip() else []
            if not target_boards:
                default_board = monday_cred.board_id if monday_cred else settings.monday_board_id
                if default_board:
                    target_boards = [default_board]
                else:
                    target_boards = ["default"]

            if is_dry_run:
                yield f"data: {json.dumps({'percent': 12, 'log': ' [Simulated] Connecting to Xero and fetching contacts...', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                now_utc = datetime.now(timezone.utc)
                mock_contacts = [
                    {"Name": "Acme Corp", "EmailAddress": "billing@acme.com", "AccountNumber": "ACT-001", "ContactID": "CON-001", "Address": "123 Industrial Way, London, EC1A 1BB", "Phones": [{"PhoneNumber": "+44 20 7946 0192"}], "UpdatedDateUTC": (now_utc - timedelta(hours=5)).isoformat() + "Z"},
                    {"Name": "Stark Industries", "EmailAddress": "finance@stark.com", "AccountNumber": "ACT-002", "ContactID": "CON-002", "Address": "10880 Malibu Point, Malibu, CA 90265", "Phones": [{"PhoneNumber": "+1 212 555 0143"}], "UpdatedDateUTC": (now_utc - timedelta(hours=4)).isoformat() + "Z"},
                    {"Name": "Wayne Enterprises", "EmailAddress": "accounts@wayne.com", "AccountNumber": "ACT-003", "ContactID": "CON-003", "Address": "1007 Mountain Drive, Gotham City, NJ 07001", "Phones": [{"PhoneNumber": "+1 312 555 0188"}], "UpdatedDateUTC": (now_utc - timedelta(hours=3)).isoformat() + "Z"},
                    {"Name": "Cyberdyne Systems", "EmailAddress": "billing@cyberdyne.com", "AccountNumber": "ACT-004", "ContactID": "CON-004", "Address": "18111 Nordhoff St, Northridge, CA 91330", "Phones": [{"PhoneNumber": "+1 415 555 0199"}], "UpdatedDateUTC": (now_utc - timedelta(hours=2)).isoformat() + "Z"},
                    {"Name": "Tyrell Corp", "EmailAddress": "finance@tyrell.com", "AccountNumber": "ACT-005", "ContactID": "CON-005", "Address": "1333 Broadway, New York, NY 10018", "Phones": [{"PhoneNumber": "+1 213 555 0101"}], "UpdatedDateUTC": (now_utc - timedelta(hours=1)).isoformat() + "Z"},
                ]
                
                if cutoff_time:
                    mock_contacts = [c for c in mock_contacts if parse_xero_date(c.get("UpdatedDateUTC")) > cutoff_time]

                total_contacts = len(mock_contacts)
                yield f"data: {json.dumps({'percent': 18, 'log': f' [Simulated] Retrieved {total_contacts} contacts from Xero after filtering.', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                contacts_to_sync = mock_contacts[:min(batch_count, total_contacts)]
                synced_count = 0

                monday_conn = None
                monday_api_key = monday_cred.api_key if monday_cred else settings.monday_api_key
                if monday_api_key:
                    try:
                        from connectors.monday_connector import MondayConnector
                        monday_conn = MondayConnector(api_key=monday_api_key)
                    except Exception as e:
                        logger.warning("Could not load MondayConnector in simulation: %s", e)

                from models.db import FieldMapping
                from engine.transformer import xero_contact_to_monday_item, xero_item_to_monday_product
                board_mappings = {}
                board_col_types = {}
                for board_id in target_boards:
                    mappings = db.query(FieldMapping).filter(FieldMapping.board_id == board_id).all() if db else []
                    if not mappings:
                        mappings = db.query(FieldMapping).filter(FieldMapping.board_id == "default").all() if db else []
                    mappings_dict = {m.target_xero_path: m.source_column for m in mappings}
                    board_mappings[board_id] = mappings_dict
                    
                    col_types = {}
                    if monday_conn:
                        try:
                            cols = monday_conn.query_board_columns(board_id)
                            col_types = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                        except Exception as e:
                            logger.warning("Could not query board columns in dry-run: %s", e)
                    board_col_types[board_id] = col_types

                # Sync Contacts (Simulated)
                for idx, contact in enumerate(contacts_to_sync):
                    contact_name = contact["Name"]
                    synced_on_boards = []
                    
                    for board_id in target_boards:
                        if monday_conn:
                            try:
                                mappings_dict = board_mappings.get(board_id)
                                item_payload = xero_contact_to_monday_item(
                                    contact,
                                    mappings=mappings_dict,
                                    column_types=board_col_types.get(board_id)
                                )
                                monday_conn.create_item(
                                    board_id=board_id,
                                    item_name=item_payload["item_name"],
                                    column_values=item_payload["column_values"]
                                )
                                synced_on_boards.append(board_id)
                            except Exception as item_err:
                                logger.error("Failed to sync contact '%s' to board %s: %s", contact_name, board_id, item_err)
                        else:
                            synced_on_boards.append(board_id)
                            
                    synced_count += 1
                    boards_label = ", ".join(synced_on_boards)
                    yield f"data: {json.dumps({'percent': 20 + int((idx + 1) / max(len(contacts_to_sync), 1) * 35), 'log': f' [Simulated] Synced customer: {contact_name} to boards [{boards_label}]', 'status': 'IN_PROGRESS'})}\n\n"
                    time.sleep(0.3)

                # Now sync Products/Items (Simulated)
                target_board_3 = board_id_3 or "5101138235"
                yield f"data: {json.dumps({'percent': 60, 'log': ' [Simulated] Connecting to Xero and fetching products/items...', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                mock_items = [
                    {"Name": "Integration Starter Pack", "Code": "PRD-001", "Description": "Entry level iPaaS connector license", "PurchaseDetails": {"UnitPrice": 45.0}, "SalesDetails": {"UnitPrice": 99.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=5)).isoformat() + "Z"},
                    {"Name": "Enterprise Data Pipeline", "Code": "PRD-002", "Description": "Unlimited throughput sync node", "PurchaseDetails": {"UnitPrice": 250.0}, "SalesDetails": {"UnitPrice": 599.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=4)).isoformat() + "Z"},
                    {"Name": "Custom Flow Consultant", "Code": "PRD-003", "Description": "Hourly specialist engineering support", "PurchaseDetails": {"UnitPrice": 75.0}, "SalesDetails": {"UnitPrice": 150.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=3)).isoformat() + "Z"},
                ]
                
                if cutoff_time:
                    mock_items = [item for item in mock_items if parse_xero_date(item.get("UpdatedDateUTC")) > cutoff_time]
                    
                total_items = len(mock_items)
                yield f"data: {json.dumps({'percent': 65, 'log': f' [Simulated] Retrieved {total_items} products/items from Xero.', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                # Load mappings for Board 3
                mappings_b3 = db.query(FieldMapping).filter(FieldMapping.board_id == target_board_3).all() if db else []
                if not mappings_b3:
                    from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                    mappings_b3 = [
                        FieldMapping(
                            user_id=user_id,
                            board_id=target_board_3,
                            target_xero_path=item["target_xero_path"],
                            source_column=item["source_column"],
                            custom_override_path=item.get("custom_override_path", "")
                        ) for item in DEFAULT_PRODUCT_MAPPINGS
                    ]
                mappings_dict_b3 = {m.target_xero_path: m.source_column for m in mappings_b3}
                
                col_types_b3 = {}
                if monday_conn and target_board_3 != "default":
                    try:
                        cols = monday_conn.query_board_columns(target_board_3)
                        col_types_b3 = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                    except Exception as e:
                        logger.warning("Could not query board 3 columns: %s", e)

                synced_items_count = 0
                for idx, item in enumerate(mock_items):
                    item_name = item["Name"]
                    if monday_conn:
                        try:
                            item_payload = xero_item_to_monday_product(
                                item,
                                mappings=mappings_dict_b3,
                                column_types=col_types_b3
                            )
                            monday_conn.create_item(
                                board_id=target_board_3,
                                item_name=item_payload["item_name"],
                                column_values=item_payload["column_values"]
                            )
                        except Exception as item_err:
                            logger.error("Failed to sync item '%s' to board %s: %s", item_name, target_board_3, item_err)
                            
                    synced_items_count += 1
                    yield f"data: {json.dumps({'percent': 65 + int((idx + 1) / max(total_items, 1) * 30), 'log': f' [Simulated] Synced product: {item_name} to board [{target_board_3}]', 'status': 'IN_PROGRESS'})}\n\n"
                    time.sleep(0.3)

                # Now sync Invoices (Simulated)
                target_board_2 = "5101138242"
                yield f"data: {json.dumps({'percent': 90, 'log': ' [Simulated] Connecting to Xero and fetching invoices...', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                mock_invoices = [
                    {
                        "InvoiceNumber": "INV-1001",
                        "InvoiceID": "111-222-333",
                        "Status": "DRAFT",
                        "Total": 1200.0,
                        "DateString": "2026-08-25",
                        "Contact": {"Name": "Acme Corp", "EmailAddress": "billing@acme.com"},
                        "LineItems": [
                            {"Description": "Consulting Services", "Quantity": 2, "UnitAmount": 500.0, "ItemCode": "PRD-003"},
                            {"Description": "Enterprise License", "Quantity": 1, "UnitAmount": 200.0, "ItemCode": "PRD-002"}
                        ]
                    }
                ]
                
                total_invoices = len(mock_invoices)
                yield f"data: {json.dumps({'percent': 92, 'log': f' [Simulated] Retrieved {total_invoices} invoices from Xero.', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.5)

                synced_invoices_count = 0
                for idx, inv in enumerate(mock_invoices):
                    inv_number = inv["InvoiceNumber"]
                    synced_invoices_count += 1
                    yield f"data: {json.dumps({'percent': 95 + int((idx + 1) / max(total_invoices, 1) * 5), 'log': f' [Simulated] Synced Invoice: {inv_number} to board [{target_board_2}]', 'status': 'IN_PROGRESS'})}\n\n"
                    time.sleep(0.3)

                total_execution_time = round(time.time() - start_time, 3)
                accuracy = 100.0
                total_synced_records = len(contacts_to_sync) + total_items + total_invoices
                latency = round((total_execution_time * 1000) / max(total_synced_records, 1), 2)

                if db:
                    try:
                        log_entry = SyncLog(
                            timestamp=datetime.now(timezone.utc),
                            status="SUCCESS",
                            direction=direction,
                            payload_count=total_synced_records,
                            error_details=f"[Simulated] Xero to Monday sync complete. Synced {synced_count} contacts, {synced_items_count} products, and {synced_invoices_count} invoices.",
                        )
                        db.add(log_entry)
                        db.commit()
                    except Exception as e:
                        logger.warning("Could not persist SyncLog entry: %s", e)

                yield f"data: {json.dumps({'percent': 100, 'log': f' [System Success] Sync execution complete! Synced {synced_count} customers, {synced_items_count} products, and {synced_invoices_count} invoices with 100.0% accuracy in {total_execution_time}s.', 'status': 'COMPLETED', 'summary': {'total': total_synced_records, 'exec_time': total_execution_time, 'latency_ms': latency, 'accuracy': accuracy}})}\n\n"
                return


            monday_api_key = monday_cred.api_key if monday_cred else settings.monday_api_key
            if not monday_api_key:
                yield f"data: {json.dumps({'percent': 0, 'log': ' Error: Monday.com API Key is not set.', 'status': 'ERROR'})}\n\n"
                return

            yield f"data: {json.dumps({'percent': 12, 'log': ' Connecting to Xero and fetching contacts...', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            from connectors.xero_connector import XeroConnector
            from connectors.monday_connector import MondayConnector
            from utils.oauth_handler import OAuthHandler
            from engine.transformer import xero_contact_to_monday_item, xero_item_to_monday_product

            oauth_handler = OAuthHandler(
                client_id=settings.xero_client_id,
                client_secret=settings.xero_client_secret,
            )
            oauth_handler._access_token = xero_cred.access_token
            oauth_handler._refresh_token = xero_cred.refresh_token
            oauth_handler._expires_at = xero_cred.token_expiry or 0.0

            # Ensure token is valid before dynamic tenant ID resolution
            try:
                oauth_handler.ensure_valid_token()
                if xero_cred and (xero_cred.access_token != oauth_handler._access_token or xero_cred.refresh_token != oauth_handler._refresh_token):
                    xero_cred.access_token = oauth_handler._access_token
                    xero_cred.refresh_token = oauth_handler._refresh_token
                    xero_cred.token_expiry = oauth_handler._expires_at
                    if db:
                        db.commit()
                        logger.info("Persisted refreshed Xero tokens to SQLite database.")
                        
                        # Also sync to mock_db.json
                        try:
                            mock_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_db.json")
                            if os.path.exists(mock_db_path):
                                with open(mock_db_path, "r") as f:
                                    mock_data = json.load(f)
                                if "credentials" in mock_data:
                                    for c in mock_data["credentials"]:
                                        if c.get("user_id") == user_id and c.get("platform_name") == "xero":
                                            c["access_token"] = oauth_handler._access_token
                                            c["refresh_token"] = oauth_handler._refresh_token
                                            c["token_expiry"] = oauth_handler._expires_at
                                            break
                                    with open(mock_db_path, "w") as f:
                                        json.dump(mock_data, f, indent=4)
                        except Exception as mock_err:
                            logger.warning("Could not sync refreshed tokens to mock_db.json: %s", mock_err)
            except Exception as token_err:
                logger.warning("Could not refresh token before dynamic tenant resolution: %s", token_err)

            # Resolve tenant ID dynamically from Xero connections
            tenant_id = None
            try:
                import requests
                conn_resp = requests.get(
                    "https://api.xero.com/connections",
                    headers={"Authorization": f"Bearer {oauth_handler._access_token}"},
                    timeout=10
                )
                conn_resp.raise_for_status()
                connections = conn_resp.json()
                if connections:
                    tenant_id = connections[0].get("tenantId")
            except Exception as conn_err:
                logger.warning("Could not dynamically resolve Xero tenant ID: %s", conn_err)

            tenant_id = tenant_id or settings.xero_tenant_id
            if not tenant_id or tenant_id == "your_xero_tenant_id_here":
                yield f"data: {json.dumps({'percent': 0, 'log': ' Error: No authorized Xero organization found. Please go to the Connectors tab, click Connect Xero, and authorize access to your Xero organization.', 'status': 'ERROR'})}\n\n"
                return
            xero_conn = XeroConnector(tenant_id=tenant_id, oauth_handler=oauth_handler)
            monday_conn = MondayConnector(api_key=monday_api_key)

            try:
                contacts = xero_conn.get_contacts()
            except Exception as e:
                yield f"data: {json.dumps({'percent': 0, 'log': f' Failed to fetch contacts from Xero: {e}', 'status': 'ERROR'})}\n\n"
                return

            if cutoff_time:
                contacts = [c for c in contacts if parse_xero_date(c.get("UpdatedDateUTC")) is None or parse_xero_date(c.get("UpdatedDateUTC")) > cutoff_time]

            total_contacts = len(contacts)
            yield f"data: {json.dumps({'percent': 18, 'log': f' Retrieved {total_contacts} contacts from Xero after filtering.', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            contacts_to_sync = contacts[:min(batch_count, total_contacts)]
            synced_count = 0
            failed_count = 0

            # Load mappings for each board
            from models.db import FieldMapping
            board_mappings = {}
            board_col_types = {}
            for board_id in target_boards:
                mappings = db.query(FieldMapping).filter(FieldMapping.board_id == board_id).all()
                if not mappings:
                    mappings = db.query(FieldMapping).filter(FieldMapping.board_id == "default").all()
                mappings_dict = {m.target_xero_path: m.source_column for m in mappings}
                board_mappings[board_id] = mappings_dict
                
                col_types = {}
                if monday_conn:
                    try:
                        cols = monday_conn.query_board_columns(board_id)
                        col_types = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                    except Exception as e:
                        logger.warning("Could not query board columns in real sync: %s", e)
                board_col_types[board_id] = col_types

            # Sync Contacts
            for idx, contact in enumerate(contacts_to_sync):
                contact_name = contact.get("Name", "Unknown")
                synced_on_boards = []
                
                for board_id in target_boards:
                    try:
                        mappings_dict = board_mappings.get(board_id)
                        item_payload = xero_contact_to_monday_item(
                            contact,
                            mappings=mappings_dict,
                            column_types=board_col_types.get(board_id)
                        )
                        monday_conn.create_item(
                            board_id=board_id,
                            item_name=item_payload["item_name"],
                            column_values=item_payload["column_values"]
                        )
                        synced_on_boards.append(board_id)
                    except Exception as item_err:
                         logger.error("Failed to sync contact '%s' to board %s: %s", contact_name, board_id, item_err)
                
                if synced_on_boards:
                    synced_count += 1
                    boards_label = ", ".join(synced_on_boards)
                    yield f"data: {json.dumps({'percent': 20 + int((idx + 1) / max(len(contacts_to_sync), 1) * 35), 'log': f' Synced customer: {contact_name} to boards [{boards_label}]', 'status': 'IN_PROGRESS'})}\n\n"
                else:
                    failed_count += 1

                time.sleep(0.05)

            # Sync Products/Items
            target_board_3 = board_id_3 or "5101138235"
            yield f"data: {json.dumps({'percent': 60, 'log': ' Fetching products/items from Xero...', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            try:
                items = xero_conn.get_items()
            except Exception as e:
                yield f"data: {json.dumps({'percent': 60, 'log': f' Failed to fetch items from Xero: {e}', 'status': 'ERROR'})}\n\n"
                return

            if cutoff_time:
                items = [it for it in items if parse_xero_date(it.get("UpdatedDateUTC")) is None or parse_xero_date(it.get("UpdatedDateUTC")) > cutoff_time]

            total_items = len(items)
            yield f"data: {json.dumps({'percent': 65, 'log': f' Retrieved {total_items} products/items from Xero.', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            # Load mappings for Board 3
            mappings_b3 = db.query(FieldMapping).filter(FieldMapping.board_id == target_board_3).all()
            if not mappings_b3:
                from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                mappings_b3 = [
                    FieldMapping(
                        user_id=user_id,
                        board_id=target_board_3,
                        target_xero_path=item["target_xero_path"],
                        source_column=item["source_column"],
                        custom_override_path=item.get("custom_override_path", "")
                    ) for item in DEFAULT_PRODUCT_MAPPINGS
                ]
            mappings_dict_b3 = {m.target_xero_path: m.source_column for m in mappings_b3}
            
            col_types_b3 = {}
            if monday_conn and target_board_3 != "default":
                try:
                    cols = monday_conn.query_board_columns(target_board_3)
                    col_types_b3 = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                except Exception as e:
                    logger.warning("Could not query board 3 columns: %s", e)

            synced_items_count = 0
            failed_items_count = 0
            items_to_sync = items[:min(batch_count, total_items)]
            for idx, item in enumerate(items_to_sync):
                item_name = item.get("Name", "Unknown Product")
                try:
                    item_payload = xero_item_to_monday_product(
                        item,
                        mappings=mappings_dict_b3,
                        column_types=col_types_b3
                    )
                    monday_conn.create_item(
                        board_id=target_board_3,
                        item_name=item_payload["item_name"],
                        column_values=item_payload["column_values"]
                    )
                    synced_items_count += 1
                    yield f"data: {json.dumps({'percent': 65 + int((idx + 1) / max(len(items_to_sync), 1) * 30), 'log': f' Synced product: {item_name} to board [{target_board_3}]', 'status': 'IN_PROGRESS'})}\n\n"
                except Exception as item_err:
                    logger.error("Failed to sync item '%s' to board %s: %s", item_name, target_board_3, item_err)
                    failed_items_count += 1
                time.sleep(0.05)

            # Now sync Invoices (Xero -> Monday)
            target_board_2 = "5101138242"
            yield f"data: {json.dumps({'percent': 90, 'log': ' Fetching invoices from Xero...', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            try:
                invoices = []
                p = 1
                while len(invoices) < batch_count:
                    chunk = xero_conn.get_invoices(page=p)
                    if not chunk:
                        break
                    invoices.extend(chunk)
                    p += 1
            except Exception as e:
                yield f"data: {json.dumps({'percent': 90, 'log': f' Failed to fetch invoices from Xero: {e}', 'status': 'ERROR'})}\n\n"
                return

            if cutoff_time:
                invoices = [inv for inv in invoices if parse_xero_date(inv.get("UpdatedDateUTC")) is None or parse_xero_date(inv.get("UpdatedDateUTC")) > cutoff_time]

            total_invoices = len(invoices)
            yield f"data: {json.dumps({'percent': 92, 'log': f' Retrieved {total_invoices} invoices from Xero.', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            # Load invoice mappings
            inv_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Invoice.InvoiceNumber").first() if db else None
            target_board_2 = inv_mapping.board_id if inv_mapping else "5101138242"
            
            col_types_b2 = {}
            if monday_conn:
                try:
                    cols = monday_conn.query_board_columns(target_board_2)
                    col_types_b2 = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                except Exception as e:
                    logger.warning("Could not query board 2 columns: %s", e)
            
            mappings_invoice = {}
            if db:
                mappings_invoice = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == target_board_2).all()}
            if not mappings_invoice:
                from routes.mappings import DEFAULT_INVOICE_MAPPINGS
                mappings_invoice = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_INVOICE_MAPPINGS}

            # Build Customer and Product lookup tables from Monday
            customer_lookup = {}
            product_lookup = {}

            # Query items from Customer board
            try:
                cust_items = monday_conn.query_board_items(board_id=board_id_1 or "default", limit=200)
                for item in cust_items:
                    c_name = item.get("name", "").strip().lower()
                    customer_lookup[c_name] = item.get("id")
                    for col in item.get("column_values", []):
                        col_title = str(col.get("title", "")).lower()
                        col_text = str(col.get("text", "")).strip().lower()
                        if "email" in col_title and col_text:
                            customer_lookup[col_text] = item.get("id")
            except Exception as e:
                logger.warning("Could not build customer lookup from Monday: %s", e)

            # Load product mappings to know the exact product code column ID
            mappings_product = {}
            if db:
                try:
                    prod_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Item.Code").first()
                    prod_board_id = prod_mapping.board_id if prod_mapping else "5101138235"
                    mappings_product = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == prod_board_id).all()}
                except Exception as map_err:
                    logger.warning("Could not pre-load product mappings: %s", map_err)
            if not mappings_product:
                from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                mappings_product = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_PRODUCT_MAPPINGS}
            product_code_col = mappings_product.get("Item.Code", "item_number").lower()

            # Query items from Product board
            try:
                prod_items = monday_conn.query_board_items(board_id=target_board_3, limit=200)
                for item in prod_items:
                    p_name = item.get("name", "").strip().lower()
                    product_lookup[p_name] = item.get("id")
                    for col in item.get("column_values", []):
                        col_id_lower = str(col.get("id", "")).lower()
                        col_title = str(col.get("title", "")).lower()
                        col_text = str(col.get("text", "")).strip().lower()
                        if col_id_lower == product_code_col or "code" in col_title or "sku" in col_title or "number" in col_title:
                            if col_text:
                                product_lookup[col_text] = item.get("id")
            except Exception as e:
                logger.warning("Could not build product lookup from Monday: %s", e)

            # Sync Invoices
            synced_invoices_count = 0
            failed_invoices_count = 0
            invoices_to_sync = invoices[:min(batch_count, total_invoices)]
            
            for idx, inv_summary in enumerate(invoices_to_sync):
                inv_id = inv_summary.get("InvoiceID")
                inv_number = inv_summary.get("InvoiceNumber", f"INV-{inv_id}")
                try:
                    # Fetch detailed invoice with line items
                    if inv_summary.get("LineItems"):
                        inv = inv_summary
                    else:
                        inv = xero_conn.get_invoice_by_id(inv_id) if inv_id else inv_summary
                    
                    # 1. Resolve customer ID
                    contact = inv.get("Contact") or {}
                    c_name = str(contact.get("Name", "")).strip().lower()
                    c_email = str(contact.get("EmailAddress", "")).strip().lower()
                    
                    customer_monday_id = customer_lookup.get(c_email) or customer_lookup.get(c_name)
                    
                    # 2. Build parent item column values
                    parent_column_values = {}
                    
                    # Status
                    status_col = mappings_invoice.get("Invoice.Status") or "status"
                    xero_status = str(inv.get("Status", "DRAFT")).upper()
                    status_type = col_types_b2.get(status_col, "text")
                    if status_type in ("color", "status"):
                        if xero_status == "PAID":
                            parent_column_values[status_col] = "Done"
                        elif xero_status in ["AUTHORISED", "SUBMITTED"]:
                            parent_column_values[status_col] = "Working on it"
                        elif xero_status == "VOIDED":
                            parent_column_values[status_col] = "Stuck"
                        else:
                            parent_column_values[status_col] = "New"
                    else:
                        parent_column_values[status_col] = xero_status
                    
                    # Date
                    date_col = mappings_invoice.get("Invoice.Date") or "date4"
                    inv_date = parse_xero_date(inv.get("DateString") or inv.get("Date"))
                    if inv_date:
                        parent_column_values[date_col] = inv_date.strftime("%Y-%m-%d")
                        
                    # Total Amount
                    total_col = mappings_invoice.get("Invoice.Total") or "numeric_mm66h8ce"
                    parent_column_values[total_col] = float(inv.get("Total", 0.0))
                    
                    # Customer Connect Boards column
                    cust_connect_col = mappings_invoice.get("Invoice.Contact.Name") or "board_relation_mm67qge0"
                    if customer_monday_id:
                        parent_column_values[cust_connect_col] = {"item_ids": [int(customer_monday_id)]}

                    # Create Invoice parent item
                    parent_res = monday_conn.create_item(
                        board_id=target_board_2,
                        item_name=inv_number,
                        column_values=parent_column_values
                    )
                    parent_item_id = parent_res.get("data", {}).get("create_item", {}).get("id")
                    
                    # 3. Create subitems (line items)
                    line_items = inv.get("LineItems") or []
                    for line in line_items:
                        line_desc = line.get("Description", "Line Item")
                        line_qty = float(line.get("Quantity", 1.0))
                        line_price = float(line.get("UnitAmount", 0.0))
                        line_code = str(line.get("ItemCode", "")).strip().lower()
                        
                        product_monday_id = product_lookup.get(line_code)
                        
                        # Subitem column values
                        sub_column_values = {}
                        
                        # Map Quantity
                        qty_col = mappings_invoice.get("Invoice.LineItems[0].Quantity") or "quantity"
                        sub_column_values[qty_col] = line_qty
                        
                        # Map Unit Price
                        price_col = mappings_invoice.get("Invoice.LineItems[0].UnitAmount") or "amount"
                        sub_column_values[price_col] = line_price
                        
                        # Product Connect Boards column
                        prod_connect_col = mappings_invoice.get("Invoice.LineItems[0].ItemCode") or "product"
                        if product_monday_id:
                            sub_column_values[prod_connect_col] = {"item_ids": [int(product_monday_id)]}
                            
                        # Create subitem
                        monday_conn.create_subitem(
                            parent_item_id=parent_item_id,
                            item_name=line_desc,
                            column_values=sub_column_values
                        )
                        
                    synced_invoices_count += 1
                    yield f"data: {json.dumps({'percent': 92 + int((idx + 1) / max(len(invoices_to_sync), 1) * 7), 'log': f' Synced Invoice: {inv_number} with {len(line_items)} subitems to board [{target_board_2}]', 'status': 'IN_PROGRESS'})}\n\n"
                except Exception as inv_err:
                    logger.error("Failed to sync invoice '%s' to Monday: %s", inv_number, inv_err)
                    failed_invoices_count += 1
                time.sleep(0.05)

            # Save refreshed Xero tokens
            try:
                xero_cred.access_token = oauth_handler._access_token
                xero_cred.refresh_token = oauth_handler._refresh_token
                xero_cred.token_expiry = oauth_handler._expires_at
                db.commit()
            except Exception as token_err:
                logger.warning("Could not persist refreshed Xero tokens: %s", token_err)

            total_execution_time = round(time.time() - start_time, 3)
            total_synced = synced_count + synced_items_count + synced_invoices_count
            total_failed = failed_count + failed_items_count + failed_invoices_count
            accuracy = round((total_synced / max(total_synced + total_failed, 1)) * 100, 1)
            total_attempted = len(contacts_to_sync) + len(items_to_sync) + len(invoices_to_sync)

            # Record SyncLog entry
            try:
                log_entry = SyncLog(
                    timestamp=datetime.now(timezone.utc),
                    status="SUCCESS" if total_failed == 0 else "PARTIAL_SUCCESS",
                    direction=direction,
                    payload_count=total_attempted,
                    error_details=f"Xero to Monday sync complete. Synced {synced_count} contacts to target boards: {', '.join(target_boards)}, {synced_items_count} products to board {target_board_3}, and {synced_invoices_count} invoices to board {target_board_2}.",
                )
                db.add(log_entry)
                db.commit()
            except Exception as e:
                logger.warning("Could not persist SyncLog entry: %s", e)

            yield f"data: {json.dumps({'percent': 100, 'log': f' Sync execution complete! Synced {synced_count} customers, {synced_items_count} products, and {synced_invoices_count} invoices with {accuracy}% accuracy in {total_execution_time}s.', 'status': 'COMPLETED', 'summary': {'total': total_attempted, 'exec_time': total_execution_time, 'latency_ms': round((total_execution_time * 1000) / max(total_attempted, 1), 2), 'accuracy': accuracy}})}\n\n"


        else:
            # Monday.com -> Xero flow
            mode_label = "B2B Grouped" if group_by_company else "Individual"

            yield f"data: {json.dumps({'percent': 5, 'log': ' Initializing M.I.R.A. Synchronization Engine...', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            if cutoff_time:
                log_msg = f" Last successful sync was at {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC. Filtering new/modified data."
                yield f"data: {json.dumps({'percent': 8, 'log': log_msg, 'status': 'IN_PROGRESS'})}\n\n"
            else:
                yield f"data: {json.dumps({'percent': 8, 'log': ' No prior successful sync found for this direction. Processing all records.', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            yield f"data: {json.dumps({'percent': 12, 'log': f' Target Xero Sales Account: {target_account} | Mode: {mode_label}', 'status': 'IN_PROGRESS'})}\n\n"
            time.sleep(0.1)

            # Retrieve board mappings
            cust_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Invoice.Contact.EmailAddress").first()
            cust_board_id = cust_mapping.board_id if cust_mapping else "default"
            prod_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Item.Code").first()
            prod_board_id = prod_mapping.board_id if prod_mapping else "5101138235"
            inv_board_id = board_id_1.strip() if board_id_1 and board_id_1.strip() else "5101138242"
            
            mappings_invoice = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == inv_board_id).all()}
            if not mappings_invoice:
                from routes.mappings import DEFAULT_INVOICE_MAPPINGS
                mappings_invoice = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_INVOICE_MAPPINGS}
            mappings_customer = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == cust_board_id).all()}
            if not mappings_customer:
                from routes.mappings import DEFAULT_MAPPINGS
                mappings_customer = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_MAPPINGS}
            mappings_product = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == prod_board_id).all()}
            if not mappings_product:
                from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                mappings_product = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_PRODUCT_MAPPINGS}

            if is_dry_run:
                # Simulated Monday -> Xero flow
                yield f"data: {json.dumps({'percent': 25, 'log': f' [Simulated] Generating {batch_count} transaction payloads...', 'status': 'IN_PROGRESS'})}\n\n"
                payloads = generate_mock_payloads(batch_count)
                
                if cutoff_time:
                    payloads = [p for p in payloads if parse_xero_date(p.get("updated_at")) is None or parse_xero_date(p.get("updated_at")) > cutoff_time]
                
                total_payloads = len(payloads)
                if total_payloads == 0:
                    yield f"data: {json.dumps({'percent': 100, 'log': ' [System Success] No transactions were created or modified since last sync. Skipping.', 'status': 'COMPLETED', 'summary': {'total': 0, 'invoices': 0, 'exec_time': round(time.time() - start_time, 3), 'latency_ms': 0.0, 'accuracy': 100.0}})}\n\n"
                    if db:
                        try:
                            log_entry = SyncLog(
                                timestamp=datetime.now(timezone.utc),
                                status="SUCCESS",
                                direction=direction,
                                payload_count=0,
                                error_details=f"[Simulated] Monday to Xero sync complete. 0 transactions to sync (all up to date).",
                            )
                            db.add(log_entry)
                            db.commit()
                        except Exception as e:
                            logger.warning("Could not persist SyncLog entry: %s", e)
                    return

                yield f"data: {json.dumps({'percent': 40, 'log': ' [Simulated] Executing DataTransformer batch mapping...', 'status': 'IN_PROGRESS'})}\n\n"
                transformer = DataTransformer()
                transform_start = time.time()

                if group_by_company:
                    res = transformer.transform_batch_grouped(payloads, mappings=mappings_invoice, customer_mappings=mappings_customer, product_mappings=mappings_product)
                    invoices = res.transformed_invoices
                else:
                    invoices = []
                    for p in payloads:
                        r = transformer.transform_record_to_invoice(p, mappings=mappings_invoice, customer_mappings=mappings_customer, product_mappings=mappings_product)
                        if r["status"] == "success":
                            invoices.append(r["invoice"])

                transform_duration_ms = (time.time() - transform_start) * 1000
                per_record_latency_ms = round(transform_duration_ms / max(total_payloads, 1), 4)

                yield f"data: {json.dumps({'percent': 70, 'log': f' [Simulated] Transformed {len(payloads)} records into {len(invoices)} Xero invoice bundles in {transform_duration_ms:.2f} ms ({per_record_latency_ms} ms/rec)', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.1)

                yield f"data: {json.dumps({'percent': 85, 'log': ' [Simulated] Pushing invoices via Xero REST API Gateway...', 'status': 'IN_PROGRESS'})}\n\n"
                time.sleep(0.1)

                total_execution_time = round(time.time() - start_time, 3)
                accuracy = 100.0

                if db:
                    try:
                        log_entry = SyncLog(
                            timestamp=datetime.now(timezone.utc),
                            status="SUCCESS",
                            direction=direction,
                            payload_count=total_payloads,
                            error_details=f"[Simulated] Processed {total_payloads} payloads ({len(invoices)} invoices) in {total_execution_time}s. Median latency: {per_record_latency_ms} ms.",
                        )
                        db.add(log_entry)
                        db.commit()
                    except Exception as e:
                        logger.warning("Could not persist SyncLog entry: %s", e)

                yield f"data: {json.dumps({'percent': 100, 'log': f' Sync execution complete! Processed {total_payloads} records with {accuracy}% accuracy in {total_execution_time}s.', 'status': 'COMPLETED', 'summary': {'total': total_payloads, 'invoices': len(invoices), 'exec_time': total_execution_time, 'latency_ms': per_record_latency_ms, 'accuracy': accuracy}})}\n\n"

            else:
                # Real Monday -> Xero flow
                yield f"data: {json.dumps({'percent': 18, 'log': ' Connecting to Monday.com and Xero API gateways...', 'status': 'IN_PROGRESS'})}\n\n"
                
                from connectors.xero_connector import XeroConnector
                from connectors.monday_connector import MondayConnector
                from utils.oauth_handler import OAuthHandler
                
                monday_api_key = monday_cred.api_key if monday_cred else settings.monday_api_key
                if not monday_api_key:
                    yield f"data: {json.dumps({'percent': 0, 'log': ' Error: Monday.com API Key is not set.', 'status': 'ERROR'})}\n\n"
                    return

                oauth_handler = OAuthHandler(client_id=settings.xero_client_id, client_secret=settings.xero_client_secret)
                oauth_handler._access_token = xero_cred.access_token
                oauth_handler._refresh_token = xero_cred.refresh_token
                oauth_handler._expires_at = xero_cred.token_expiry or 0.0
                
                try:
                    oauth_handler.ensure_valid_token()
                    if xero_cred and (xero_cred.access_token != oauth_handler._access_token or xero_cred.refresh_token != oauth_handler._refresh_token):
                        xero_cred.access_token = oauth_handler._access_token
                        xero_cred.refresh_token = oauth_handler._refresh_token
                        xero_cred.token_expiry = oauth_handler._expires_at
                        db.commit()
                except Exception as token_err:
                    logger.warning("Could not refresh token: %s", token_err)
                
                tenant_id = None
                try:
                    import requests
                    conn_resp = requests.get("https://api.xero.com/connections", headers={"Authorization": f"Bearer {oauth_handler._access_token}"}, timeout=10)
                    conn_resp.raise_for_status()
                    connections = conn_resp.json()
                    if connections:
                        tenant_id = connections[0].get("tenantId")
                except Exception as conn_err:
                    logger.warning("Could not dynamically resolve tenant: %s", conn_err)
                
                tenant_id = tenant_id or settings.xero_tenant_id
                if not tenant_id or tenant_id == "your_xero_tenant_id_here":
                    yield f"data: {json.dumps({'percent': 0, 'log': ' Error: No authorized Xero organization found. Please go to the Connectors tab, click Connect Xero, and authorize access to your Xero organization.', 'status': 'ERROR'})}\n\n"
                    return
                xero_conn = XeroConnector(tenant_id=tenant_id, oauth_handler=oauth_handler)
                monday_conn = MondayConnector(api_key=monday_api_key)
                
                yield f"data: {json.dumps({'percent': 25, 'log': f' Querying items from Monday.com Invoice Board {inv_board_id}...', 'status': 'IN_PROGRESS'})}\n\n"
                
                try:
                    board_items = monday_conn.query_board_items(board_id=inv_board_id, limit=batch_count)
                except Exception as e:
                    yield f"data: {json.dumps({'percent': 25, 'log': f' Failed to fetch items from Monday.com: {e}', 'status': 'ERROR'})}\n\n"
                    return
                
                yield f"data: {json.dumps({'percent': 45, 'log': f' Retrieved {len(board_items)} items from board. Transforming to Xero Invoices...', 'status': 'IN_PROGRESS'})}\n\n"
                
                transformed_invoices = []
                validation_failures = []
                
                status_col_id = mappings_invoice.get("Invoice.Status") or "status"
                
                for idx, item in enumerate(board_items):
                    status_val = ""
                    for col in item.get("column_values", []):
                        if str(col.get("id", "")).lower() == status_col_id.lower():
                            status_val = str(col.get("text", "")).strip()
                    
                    if status_val in ("Synced to Xero", "Paid"):
                        continue
                    
                    res = transformer.transform_record_to_invoice(
                        item,
                        mappings=mappings_invoice,
                        customer_mappings=mappings_customer,
                        product_mappings=mappings_product,
                    )
                    if res["status"] == "success":
                        transformed_invoices.append((item.get("id"), res["invoice"]))
                    else:
                        validation_failures.append(res)
                
                total_to_sync = len(transformed_invoices)
                yield f"data: {json.dumps({'percent': 65, 'log': f' Mapped {total_to_sync} pending invoices. Submitting to Xero API...', 'status': 'IN_PROGRESS'})}\n\n"
                
                synced_count = 0
                failed_count = len(validation_failures)
                
                for idx, (monday_id, inv_model) in enumerate(transformed_invoices):
                    try:
                        # 1. Process/Sync Contact (Customer)
                        try:
                            xero_conn.create_or_update_contact(inv_model.Contact)
                        except Exception as c_err:
                            logger.warning("Contact sync failed (non-fatal, continuing): %s", c_err)
                            
                        # 2. Process/Ensure Products (Items) exist in Xero
                        for line in inv_model.LineItems:
                            if line.ItemCode:
                                try:
                                    from models.schemas import Product as SchemaProduct
                                    prod_model = SchemaProduct(
                                        Code=line.ItemCode,
                                        Name=line.description or line.ItemCode,
                                        UnitPrice=line.UnitAmount,
                                        Description=line.description
                                    )
                                    xero_conn.create_product_item(prod_model)
                                except Exception as p_err:
                                    logger.warning("Product sync warning: %s", p_err)
                                    
                        # 3. Process Invoice
                        xero_conn.create_invoice(inv_model)
                        monday_conn.update_item_status(monday_id, "Synced to Xero", board_id=inv_board_id)
                        monday_conn.post_item_update(monday_id, f" Successfully synced to Xero!\n• Invoice Number: {inv_model.invoice_number or 'INV-SUCCESS'}\n• Amount: £{inv_model.TotalAmount:.2f}")
                        synced_count += 1
                        
                        yield f"data: {json.dumps({'percent': 65 + int((idx + 1) / max(total_to_sync, 1) * 30), 'log': f' Synced Invoice for {inv_model.Contact.Name} (£{inv_model.TotalAmount:.2f})', 'status': 'IN_PROGRESS'})}\n\n"
                    except Exception as x_err:
                        monday_conn.update_item_status(monday_id, "Sync Error", board_id=inv_board_id)
                        monday_conn.post_item_update(monday_id, f" Xero Sync Error:\n{x_err}")
                        failed_count += 1
                        yield f"data: {json.dumps({'percent': 65 + int((idx + 1) / max(total_to_sync, 1) * 30), 'log': f' Failed to sync invoice for item {monday_id}: {x_err}', 'status': 'IN_PROGRESS'})}\n\n"
                    time.sleep(0.1)
                
                accuracy = round((synced_count / max(synced_count + failed_count, 1)) * 100, 1)
                total_execution_time = round(time.time() - start_time, 3)
                
                if db:
                    try:
                        log_entry = SyncLog(
                            timestamp=datetime.now(timezone.utc),
                            status="SUCCESS" if failed_count == 0 else "PARTIAL_SUCCESS",
                            direction=direction,
                            payload_count=synced_count + failed_count,
                            error_details=f"Monday to Xero sync complete. Synced {synced_count} invoices from board {inv_board_id}. {failed_count} failures.",
                        )
                        db.add(log_entry)
                        db.commit()
                    except Exception as e:
                        logger.warning("Could not persist SyncLog entry: %s", e)
                
                yield f"data: {json.dumps({'percent': 100, 'log': f' Sync execution complete! Synced {synced_count} invoices with {accuracy}% accuracy in {total_execution_time}s.', 'status': 'COMPLETED', 'summary': {'total': synced_count + failed_count, 'invoices': synced_count, 'exec_time': total_execution_time, 'latency_ms': round((total_execution_time * 1000) / max(synced_count + failed_count, 1), 2), 'accuracy': accuracy}})}\n\n"

    def event_generator() -> Generator[str, None, None]:
        from models.db import SessionLocal, MockDBSession, has_sqlalchemy
        db = None
        try:
            if has_sqlalchemy and SessionLocal:
                db = SessionLocal()
        except Exception:
            db = None
        if not db:
            db = MockDBSession()
        try:
            yield from _event_generator_run(db)
        finally:
            if db and hasattr(db, "close"):
                db.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/api/sync/run")
async def run_sync_engine(
    request: Request,
    direction: str = Form("xero_to_monday"),
    board_id_1: Optional[str] = Form(None),
    batch_count: int = Form(500),
    group_by_company: bool = Form(True),
    target_account: str = Form("200"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    HTTP POST endpoint triggering batch sync engine execution.
    """
    return RedirectResponse(
        url="/sync?success=Sync+operation+triggered.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# Shared memory for SPA logs
SPA_LOGS = []

def run_spa_sync_in_background(
    direction: str,
    board_id_1: Optional[str],
    board_id_3: Optional[str],
    batch_count: int,
    group_by_company: bool,
    target_account: str,
    user_id: int,
):
    global SPA_LOGS
    SPA_LOGS.clear()
    
    start_time = time.time()
    
    def log(msg, level="INFO"):
        SPA_LOGS.append({
            "timestamp": time.time(),
            "level": level,
            "message": msg
        })

    log(" Initializing SPA Sync Pipeline...")
    time.sleep(0.1)

    from models.db import SessionLocal, Credentials, FieldMapping, SyncLog
    from engine.transformer import DataTransformer
    
    db = SessionLocal()
    try:
        # Fetch last successful sync time
        last_sync = None
        cutoff_time = None
        if db:
            try:
                last_sync = (
                    db.query(SyncLog)
                    .filter(
                        SyncLog.direction == direction,
                        SyncLog.status.in_(["SUCCESS", "PARTIAL_SUCCESS"])
                    )
                    .order_by(SyncLog.timestamp.desc())
                    .first()
                )
            except Exception as e:
                logger.warning("Could not fetch last SyncLog in SPA background: %s", e)

        if last_sync and last_sync.timestamp:
            cutoff_time = last_sync.timestamp
            if cutoff_time.tzinfo is None:
                cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

        if cutoff_time:
            log(f" Last successful sync was at {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} UTC. Filtering new/modified data.")
        else:
            log(" No prior successful sync found for this direction. Processing all records.")

        xero_cred = get_user_credentials(db, user_id, "xero")
        monday_cred = get_user_credentials(db, user_id, "monday")
        is_dry_run = (os.getenv("XERO_DRY_RUN") == "True") or (not xero_cred or not xero_cred.access_token)
        transformer = DataTransformer()

        if direction == "xero_to_monday":
            log(" Connecting to Xero and fetching contacts...")
            time.sleep(0.2)

            target_boards = [board_id_1.strip()] if board_id_1 and board_id_1.strip() else []
            if not target_boards:
                target_boards = ["default"]

            target_board_3 = board_id_3 or "5101138235"

            if is_dry_run:
                log(" [Simulated] Retrieving contacts from Xero...")
                time.sleep(0.3)
                now_utc = datetime.now(timezone.utc)
                mock_contacts = [
                    {"Name": "Acme Corp", "EmailAddress": "billing@acme.com", "AccountNumber": "ACT-001", "ContactID": "CON-001", "Address": "123 Industrial Way, London, EC1A 1BB", "Phones": [{"PhoneNumber": "+44 20 7946 0192"}], "UpdatedDateUTC": (now_utc - timedelta(hours=5)).isoformat() + "Z"},
                    {"Name": "Stark Industries", "EmailAddress": "finance@stark.com", "AccountNumber": "ACT-002", "ContactID": "CON-002", "Address": "10880 Malibu Point, Malibu, CA 90265", "Phones": [{"PhoneNumber": "+1 212 555 0143"}], "UpdatedDateUTC": (now_utc - timedelta(hours=4)).isoformat() + "Z"},
                    {"Name": "Wayne Enterprises", "EmailAddress": "accounts@wayne.com", "AccountNumber": "ACT-003", "ContactID": "CON-003", "Address": "1007 Mountain Drive, Gotham City, NJ 07001", "Phones": [{"PhoneNumber": "+1 312 555 0188"}], "UpdatedDateUTC": (now_utc - timedelta(hours=3)).isoformat() + "Z"},
                    {"Name": "Cyberdyne Systems", "EmailAddress": "billing@cyberdyne.com", "AccountNumber": "ACT-004", "ContactID": "CON-004", "Address": "18111 Nordhoff St, Northridge, CA 91330", "Phones": [{"PhoneNumber": "+1 415 555 0199"}], "UpdatedDateUTC": (now_utc - timedelta(hours=2)).isoformat() + "Z"},
                    {"Name": "Tyrell Corp", "EmailAddress": "finance@tyrell.com", "AccountNumber": "ACT-005", "ContactID": "CON-005", "Address": "1333 Broadway, New York, NY 10018", "Phones": [{"PhoneNumber": "+1 213 555 0101"}], "UpdatedDateUTC": (now_utc - timedelta(hours=1)).isoformat() + "Z"},
                ]
                
                if cutoff_time:
                    mock_contacts = [c for c in mock_contacts if parse_xero_date(c.get("UpdatedDateUTC")) > cutoff_time]
                
                log(f" [Simulated] Retrieved {len(mock_contacts)} contacts from Xero after filtering.")
                
                synced_count = 0
                for contact in mock_contacts[:batch_count]:
                    log(f" [Simulated] Synced customer: {contact['Name']} to boards [{', '.join(target_boards)}]")
                    synced_count += 1
                    time.sleep(0.2)

                log(" [Simulated] Connecting to Xero and fetching products/items...")
                time.sleep(0.3)
                mock_items = [
                    {"Name": "Integration Starter Pack", "Code": "PRD-001", "Description": "Entry level iPaaS connector license", "PurchaseDetails": {"UnitPrice": 45.0}, "SalesDetails": {"UnitPrice": 99.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=5)).isoformat() + "Z"},
                    {"Name": "Enterprise Data Pipeline", "Code": "PRD-002", "Description": "Unlimited throughput sync node", "PurchaseDetails": {"UnitPrice": 250.0}, "SalesDetails": {"UnitPrice": 599.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=4)).isoformat() + "Z"},
                    {"Name": "Custom Flow Consultant", "Code": "PRD-003", "Description": "Hourly specialist engineering support", "PurchaseDetails": {"UnitPrice": 75.0}, "SalesDetails": {"UnitPrice": 150.0}, "UpdatedDateUTC": (now_utc - timedelta(hours=3)).isoformat() + "Z"},
                ]
                if cutoff_time:
                    mock_items = [it for it in mock_items if parse_xero_date(it.get("UpdatedDateUTC")) > cutoff_time]
                
                log(f" [Simulated] Retrieved {len(mock_items)} products/items from Xero.")
                
                synced_items_count = 0
                for item in mock_items[:batch_count]:
                    log(f" [Simulated] Synced product: {item['Name']} to board [{target_board_3}]")
                    synced_items_count += 1
                    time.sleep(0.2)

                # Now sync Invoices (Simulated)
                target_board_2 = "5101138242"
                log(" [Simulated] Connecting to Xero and fetching invoices...")
                time.sleep(0.3)

                mock_invoices = [
                    {
                        "InvoiceNumber": "INV-1001",
                        "InvoiceID": "111-222-333",
                        "Status": "DRAFT",
                        "Total": 1200.0,
                        "DateString": "2026-08-25",
                        "Contact": {"Name": "Acme Corp", "EmailAddress": "billing@acme.com"},
                        "LineItems": [
                            {"Description": "Consulting Services", "Quantity": 2, "UnitAmount": 500.0, "ItemCode": "PRD-003"},
                            {"Description": "Enterprise License", "Quantity": 1, "UnitAmount": 200.0, "ItemCode": "PRD-002"}
                        ]
                    }
                ]
                
                total_invoices = len(mock_invoices)
                log(f" [Simulated] Retrieved {total_invoices} invoices from Xero.")
                time.sleep(0.3)

                synced_invoices_count = 0
                for idx, inv in enumerate(mock_invoices):
                    inv_number = inv["InvoiceNumber"]
                    synced_invoices_count += 1
                    log(f" [Simulated] Synced Invoice: {inv_number} to board [{target_board_2}]")
                    time.sleep(0.2)

                exec_time = round(time.time() - start_time, 3)
                log(f"[System Success] Sync execution complete! Synced {synced_count} customers, {synced_items_count} products, and {synced_invoices_count} invoices in {exec_time}s.")
                
                try:
                    log_entry = SyncLog(
                        timestamp=datetime.now(timezone.utc),
                        status="SUCCESS",
                        direction=direction,
                        payload_count=synced_count + synced_items_count + synced_invoices_count,
                        error_details=f"[Simulated SPA] Xero to Monday sync complete. Synced {synced_count} contacts, {synced_items_count} products, and {synced_invoices_count} invoices.",
                    )
                    db.add(log_entry)
                    db.commit()
                except Exception as db_err:
                    logger.warning("Could not persist SyncLog in SPA background: %s", db_err)
            else:
                from connectors.xero_connector import XeroConnector
                from connectors.monday_connector import MondayConnector
                from utils.oauth_handler import OAuthHandler
                from engine.transformer import xero_contact_to_monday_item, xero_item_to_monday_product
                
                monday_api_key = monday_cred.api_key if monday_cred else settings.monday_api_key
                
                oauth_handler = OAuthHandler(
                    client_id=settings.xero_client_id,
                    client_secret=settings.xero_client_secret,
                )
                oauth_handler._access_token = xero_cred.access_token
                oauth_handler._refresh_token = xero_cred.refresh_token
                oauth_handler._expires_at = xero_cred.token_expiry or 0.0

                try:
                    oauth_handler.ensure_valid_token()
                    if xero_cred and (xero_cred.access_token != oauth_handler._access_token or xero_cred.refresh_token != oauth_handler._refresh_token):
                        xero_cred.access_token = oauth_handler._access_token
                        xero_cred.refresh_token = oauth_handler._refresh_token
                        xero_cred.token_expiry = oauth_handler._expires_at
                        if db:
                            db.commit()
                except Exception as token_err:
                    logger.warning("Could not refresh token: %s", token_err)

                tenant_id = None
                try:
                    import requests
                    conn_resp = requests.get(
                        "https://api.xero.com/connections",
                        headers={"Authorization": f"Bearer {oauth_handler._access_token}"},
                        timeout=10
                    )
                    conn_resp.raise_for_status()
                    connections = conn_resp.json()
                    if connections:
                        tenant_id = connections[0].get("tenantId")
                except Exception as conn_err:
                    logger.warning("Could not dynamically resolve Xero tenant ID: %s", conn_err)

                tenant_id = tenant_id or settings.xero_tenant_id
                if not tenant_id or tenant_id == "your_xero_tenant_id_here":
                    log(" Error: No authorized Xero organization found. Please go to the Connectors tab, click Connect Xero, and authorize access to your Xero organization.", "ERROR")
                    return
                xero_conn = XeroConnector(tenant_id=tenant_id, oauth_handler=oauth_handler)
                monday_conn = MondayConnector(api_key=monday_api_key)

                contacts = xero_conn.get_contacts()
                if cutoff_time:
                    contacts = [c for c in contacts if parse_xero_date(c.get("UpdatedDateUTC")) is None or parse_xero_date(c.get("UpdatedDateUTC")) > cutoff_time]
                log(f" Retrieved {len(contacts)} contacts from Xero after filtering.")
                
                board_mappings = {}
                board_col_types = {}
                for board_id in target_boards:
                    mappings = db.query(FieldMapping).filter(FieldMapping.board_id == board_id).all()
                    if not mappings:
                        mappings = db.query(FieldMapping).filter(FieldMapping.board_id == "default").all()
                    board_mappings[board_id] = {m.target_xero_path: m.source_column for m in mappings}
                    
                    col_types = {}
                    if monday_conn:
                        try:
                            cols = monday_conn.query_board_columns(board_id)
                            col_types = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                        except Exception as e:
                            logger.warning("Could not query board columns in background sync: %s", e)
                    board_col_types[board_id] = col_types

                synced_count = 0
                failed_count = 0
                for contact in contacts[:batch_count]:
                    contact_name = contact.get("Name", "Unknown")
                    synced_ok = False
                    for board_id in target_boards:
                        try:
                            mappings_dict = board_mappings.get(board_id)
                            item_payload = xero_contact_to_monday_item(
                                contact,
                                mappings=mappings_dict,
                                column_types=board_col_types.get(board_id)
                            )
                            monday_conn.create_item(
                                board_id=board_id,
                                item_name=item_payload["item_name"],
                                column_values=item_payload["column_values"]
                            )
                            synced_ok = True
                        except Exception as item_err:
                            log(f" Failed to sync contact '{contact_name}' to board {board_id}: {item_err}", "ERROR")
                    if synced_ok:
                        synced_count += 1
                        log(f" Synced customer: {contact_name} to boards [{', '.join(target_boards)}]")
                    else:
                        failed_count += 1
                    time.sleep(0.1)

                log(" Fetching products/items from Xero...")
                items = xero_conn.get_items()
                if cutoff_time:
                    items = [it for it in items if parse_xero_date(it.get("UpdatedDateUTC")) is None or parse_xero_date(it.get("UpdatedDateUTC")) > cutoff_time]
                log(f" Retrieved {len(items)} products/items from Xero.")

                mappings_b3 = db.query(FieldMapping).filter(FieldMapping.board_id == target_board_3).all()
                if not mappings_b3:
                    from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                    mappings_b3 = [
                        FieldMapping(
                            user_id=user_id,
                            board_id=target_board_3,
                            target_xero_path=item["target_xero_path"],
                            source_column=item["source_column"],
                            custom_override_path=item.get("custom_override_path", "")
                        ) for item in DEFAULT_PRODUCT_MAPPINGS
                    ]
                mappings_dict_b3 = {m.target_xero_path: m.source_column for m in mappings_b3}
                
                col_types_b3 = {}
                if monday_conn and target_board_3 != "default":
                    try:
                        cols = monday_conn.query_board_columns(target_board_3)
                        col_types_b3 = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                    except Exception as e:
                        logger.warning("Could not query board 3 columns: %s", e)

                synced_items_count = 0
                failed_items_count = 0
                for item in items[:batch_count]:
                    item_name = item.get("Name", "Unknown Product")
                    try:
                        item_payload = xero_item_to_monday_product(
                            item,
                            mappings=mappings_dict_b3,
                            column_types=col_types_b3
                        )
                        monday_conn.create_item(
                            board_id=target_board_3,
                            item_name=item_payload["item_name"],
                            column_values=item_payload["column_values"]
                        )
                        synced_items_count += 1
                        log(f" Synced product: {item_name} to board [{target_board_3}]")
                    except Exception as item_err:
                        log(f" Failed to sync product '{item_name}' to board {target_board_3}: {item_err}", "ERROR")
                        failed_items_count += 1
                    time.sleep(0.1)

                # Now sync Invoices (Xero -> Monday)
                target_board_2 = "5101138242"
                log(" Fetching invoices from Xero...")
                time.sleep(0.1)

                try:
                    invoices = []
                    p = 1
                    while len(invoices) < batch_count:
                        chunk = xero_conn.get_invoices(page=p)
                        if not chunk:
                            break
                        invoices.extend(chunk)
                        p += 1
                except Exception as e:
                    log(f" Failed to fetch invoices from Xero: {e}", "ERROR")
                    return

                if cutoff_time:
                    invoices = [inv for inv in invoices if parse_xero_date(inv.get("UpdatedDateUTC")) is None or parse_xero_date(inv.get("UpdatedDateUTC")) > cutoff_time]

                total_invoices = len(invoices)
                log(f" Retrieved {total_invoices} invoices from Xero.")
                time.sleep(0.1)

                # Load invoice mappings
                inv_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Invoice.InvoiceNumber").first() if db else None
                target_board_2 = inv_mapping.board_id if inv_mapping else "5101138242"
                
                col_types_b2 = {}
                if monday_conn:
                    try:
                        cols = monday_conn.query_board_columns(target_board_2)
                        col_types_b2 = {c["id"]: c["type"] for c in cols if "id" in c and "type" in c}
                    except Exception as e:
                        logger.warning("Could not query board 2 columns in background sync: %s", e)

                mappings_invoice = {}
                if db:
                    mappings_invoice = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == target_board_2).all()}
                if not mappings_invoice:
                    from routes.mappings import DEFAULT_INVOICE_MAPPINGS
                    mappings_invoice = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_INVOICE_MAPPINGS}

                # Build Customer and Product lookup tables from Monday
                customer_lookup = {}
                product_lookup = {}

                # Query items from Customer board
                try:
                    cust_items = monday_conn.query_board_items(board_id=board_id_1 or "default", limit=200)
                    for item in cust_items:
                        c_name = item.get("name", "").strip().lower()
                        customer_lookup[c_name] = item.get("id")
                        for col in item.get("column_values", []):
                            col_title = str(col.get("title", "")).lower()
                            col_text = str(col.get("text", "")).strip().lower()
                            if "email" in col_title and col_text:
                                customer_lookup[col_text] = item.get("id")
                except Exception as e:
                    logger.warning("Could not build customer lookup from Monday: %s", e)

                # Load product mappings to know the exact product code column ID
                mappings_product = {}
                if db:
                    try:
                        prod_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Item.Code").first()
                        prod_board_id = prod_mapping.board_id if prod_mapping else "5101138235"
                        mappings_product = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == prod_board_id).all()}
                    except Exception as map_err:
                        logger.warning("Could not pre-load product mappings: %s", map_err)
                if not mappings_product:
                    from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                    mappings_product = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_PRODUCT_MAPPINGS}
                product_code_col = mappings_product.get("Item.Code", "item_number").lower()

                # Query items from Product board
                try:
                    prod_items = monday_conn.query_board_items(board_id=target_board_3, limit=200)
                    for item in prod_items:
                        p_name = item.get("name", "").strip().lower()
                        product_lookup[p_name] = item.get("id")
                        for col in item.get("column_values", []):
                            col_id_lower = str(col.get("id", "")).lower()
                            col_title = str(col.get("title", "")).lower()
                            col_text = str(col.get("text", "")).strip().lower()
                            if col_id_lower == product_code_col or "code" in col_title or "sku" in col_title or "number" in col_title:
                                if col_text:
                                    product_lookup[col_text] = item.get("id")
                except Exception as e:
                    logger.warning("Could not build product lookup from Monday: %s", e)

                # Sync Invoices
                synced_invoices_count = 0
                failed_invoices_count = 0
                invoices_to_sync = invoices[:min(batch_count, total_invoices)]
                
                for idx, inv_summary in enumerate(invoices_to_sync):
                    inv_id = inv_summary.get("InvoiceID")
                    inv_number = inv_summary.get("InvoiceNumber", f"INV-{inv_id}")
                    try:
                        # Fetch detailed invoice with line items
                        if inv_summary.get("LineItems"):
                            inv = inv_summary
                        else:
                            inv = xero_conn.get_invoice_by_id(inv_id) if inv_id else inv_summary
                        
                        # 1. Resolve customer ID
                        contact = inv.get("Contact") or {}
                        c_name = str(contact.get("Name", "")).strip().lower()
                        c_email = str(contact.get("EmailAddress", "")).strip().lower()
                        
                        customer_monday_id = customer_lookup.get(c_email) or customer_lookup.get(c_name)
                        
                        # 2. Build parent item column values
                        parent_column_values = {}
                        
                        # Status
                        status_col = mappings_invoice.get("Invoice.Status") or "status"
                        xero_status = str(inv.get("Status", "DRAFT")).upper()
                        status_type = col_types_b2.get(status_col, "text")
                        if status_type in ("color", "status"):
                            if xero_status == "PAID":
                                parent_column_values[status_col] = "Done"
                            elif xero_status in ["AUTHORISED", "SUBMITTED"]:
                                parent_column_values[status_col] = "Working on it"
                            elif xero_status == "VOIDED":
                                parent_column_values[status_col] = "Stuck"
                            else:
                                parent_column_values[status_col] = "New"
                        else:
                            parent_column_values[status_col] = xero_status
                        
                        # Date
                        date_col = mappings_invoice.get("Invoice.Date") or "date4"
                        inv_date = parse_xero_date(inv.get("DateString") or inv.get("Date"))
                        if inv_date:
                            parent_column_values[date_col] = inv_date.strftime("%Y-%m-%d")
                            
                        # Total Amount
                        total_col = mappings_invoice.get("Invoice.Total") or "numeric_mm66h8ce"
                        parent_column_values[total_col] = float(inv.get("Total", 0.0))
                        
                        # Customer Connect Boards column
                        cust_connect_col = mappings_invoice.get("Invoice.Contact.Name") or "board_relation_mm67qge0"
                        if customer_monday_id:
                            parent_column_values[cust_connect_col] = {"item_ids": [int(customer_monday_id)]}

                        # Create Invoice parent item
                        parent_res = monday_conn.create_item(
                            board_id=target_board_2,
                            item_name=inv_number,
                            column_values=parent_column_values
                        )
                        parent_item_id = parent_res.get("data", {}).get("create_item", {}).get("id")
                        
                        # 3. Create subitems (line items)
                        line_items = inv.get("LineItems") or []
                        for line in line_items:
                            line_desc = line.get("Description", "Line Item")
                            line_qty = float(line.get("Quantity", 1.0))
                            line_price = float(line.get("UnitAmount", 0.0))
                            line_code = str(line.get("ItemCode", "")).strip().lower()
                            
                            product_monday_id = product_lookup.get(line_code)
                            log(f"Line item code: '{line_code}' -> Resolved product_monday_id: {product_monday_id}")
                            
                            # Subitem column values
                            sub_column_values = {}
                            
                            # Map Quantity
                            qty_col = mappings_invoice.get("Invoice.LineItems[0].Quantity") or "quantity"
                            sub_column_values[qty_col] = line_qty
                            
                            # Map Unit Price
                            price_col = mappings_invoice.get("Invoice.LineItems[0].UnitAmount") or "amount"
                            sub_column_values[price_col] = line_price
                            
                            # Product Connect Boards column
                            prod_connect_col = mappings_invoice.get("Invoice.LineItems[0].ItemCode") or "product"
                            if product_monday_id:
                                sub_column_values[prod_connect_col] = {"item_ids": [int(product_monday_id)]}
                                
                            # Create subitem
                            monday_conn.create_subitem(
                                parent_item_id=parent_item_id,
                                item_name=line_desc,
                                column_values=sub_column_values
                            )
                            
                        synced_invoices_count += 1
                        log(f" Synced Invoice: {inv_number} with {len(line_items)} subitems to board [{target_board_2}]")
                    except Exception as inv_err:
                        log(f" Failed to sync invoice '{inv_number}' to Monday: {inv_err}", "ERROR")
                        failed_invoices_count += 1
                    time.sleep(0.05)

                exec_time = round(time.time() - start_time, 3)
                log(f"[System Success] Sync execution complete! Synced {synced_count} customers, {synced_items_count} products, and {synced_invoices_count} invoices in {exec_time}s.")
                
                try:
                    log_entry = SyncLog(
                        timestamp=datetime.now(timezone.utc),
                        status="SUCCESS" if (failed_count + failed_items_count + failed_invoices_count) == 0 else "PARTIAL_SUCCESS",
                        direction=direction,
                        payload_count=synced_count + synced_items_count + synced_invoices_count,
                        error_details=f"SPA Sync complete. Synced {synced_count} contacts, {synced_items_count} products, and {synced_invoices_count} invoices.",
                    )
                    db.add(log_entry)
                    db.commit()
                except Exception as db_err:
                    logger.warning("Could not persist SyncLog in SPA background: %s", db_err)
        else:
            # Monday.com -> Xero flow
            log(f" Target Xero Sales Account: {target_account} | Mode: B2B Grouped")
            
            cust_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Invoice.Contact.EmailAddress").first()
            cust_board_id = cust_mapping.board_id if cust_mapping else "default"
            prod_mapping = db.query(FieldMapping).filter(FieldMapping.target_xero_path == "Item.Code").first()
            prod_board_id = prod_mapping.board_id if prod_mapping else "5101138235"
            inv_board_id = board_id_1.strip() if board_id_1 and board_id_1.strip() else "5101138242"
            
            mappings_invoice = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == inv_board_id).all()}
            if not mappings_invoice:
                from routes.mappings import DEFAULT_INVOICE_MAPPINGS
                mappings_invoice = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_INVOICE_MAPPINGS}
            mappings_customer = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == cust_board_id).all()}
            if not mappings_customer:
                from routes.mappings import DEFAULT_MAPPINGS
                mappings_customer = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_MAPPINGS}
            mappings_product = {m.target_xero_path: m.source_column for m in db.query(FieldMapping).filter(FieldMapping.board_id == prod_board_id).all()}
            if not mappings_product:
                from routes.mappings import DEFAULT_PRODUCT_MAPPINGS
                mappings_product = {m["target_xero_path"]: m["source_column"] for m in DEFAULT_PRODUCT_MAPPINGS}

            if is_dry_run:
                log(f" Generating {batch_count} transaction payloads...")
                payloads = generate_mock_payloads(batch_count)
                if cutoff_time:
                    payloads = [p for p in payloads if parse_xero_date(p.get("updated_at")) is None or parse_xero_date(p.get("updated_at")) > cutoff_time]

                total_payloads = len(payloads)
                if total_payloads == 0:
                    exec_time = round(time.time() - start_time, 3)
                    log(f"[System Success] Sync execution complete! Processed 0 records in {exec_time}s.")
                    try:
                        log_entry = SyncLog(
                            timestamp=datetime.now(timezone.utc),
                            status="SUCCESS",
                            direction=direction,
                            payload_count=0,
                            error_details=f"[Simulated SPA] Monday to Xero sync complete. 0 transactions to sync (all up to date).",
                        )
                        db.add(log_entry)
                        db.commit()
                    except Exception as db_err:
                        logger.warning("Could not persist SyncLog: %s", db_err)
                    return

                time.sleep(0.2)
                log(" Executing DataTransformer batch mapping...")
                if group_by_company:
                    res = transformer.transform_batch_grouped(payloads, mappings=mappings_invoice, customer_mappings=mappings_customer, product_mappings=mappings_product)
                    invoices = res.transformed_invoices
                else:
                    invoices = []
                    for p in payloads:
                        r = transformer.transform_record_to_invoice(p, mappings=mappings_invoice, customer_mappings=mappings_customer, product_mappings=mappings_product)
                        if r["status"] == "success":
                            invoices.append(r["invoice"])
                
                time.sleep(0.2)
                log(f" Transformed {total_payloads} records into {len(invoices)} Xero invoice bundles.")
                time.sleep(0.1)
                log(" Pushing invoices via Xero REST API Gateway...")
                time.sleep(0.2)
                exec_time = round(time.time() - start_time, 3)
                log(f"[System Success] Sync execution complete! Processed {total_payloads} records in {exec_time}s.")
                
                try:
                    log_entry = SyncLog(
                        timestamp=datetime.now(timezone.utc),
                        status="SUCCESS",
                        direction=direction,
                        payload_count=total_payloads,
                        error_details=f"[Simulated SPA] Monday to Xero sync complete. Processed {total_payloads} records.",
                    )
                    db.add(log_entry)
                    db.commit()
                except Exception as db_err:
                    logger.warning("Could not persist SyncLog: %s", db_err)
            else:
                log(" Connecting to Monday.com and Xero API gateways...")
                from connectors.xero_connector import XeroConnector
                from connectors.monday_connector import MondayConnector
                from utils.oauth_handler import OAuthHandler
                
                monday_api_key = monday_cred.api_key if monday_cred else settings.monday_api_key
                if not monday_api_key:
                    log(" Error: Monday.com API Key is not set.", "ERROR")
                    return

                oauth_handler = OAuthHandler(client_id=settings.xero_client_id, client_secret=settings.xero_client_secret)
                oauth_handler._access_token = xero_cred.access_token
                oauth_handler._refresh_token = xero_cred.refresh_token
                oauth_handler._expires_at = xero_cred.token_expiry or 0.0
                
                try:
                    oauth_handler.ensure_valid_token()
                    if xero_cred and (xero_cred.access_token != oauth_handler._access_token or xero_cred.refresh_token != oauth_handler._refresh_token):
                        xero_cred.access_token = oauth_handler._access_token
                        xero_cred.refresh_token = oauth_handler._refresh_token
                        xero_cred.token_expiry = oauth_handler._expires_at
                        db.commit()
                except Exception as token_err:
                    logger.warning("Could not refresh token: %s", token_err)
                
                tenant_id = None
                try:
                    import requests
                    conn_resp = requests.get("https://api.xero.com/connections", headers={"Authorization": f"Bearer {oauth_handler._access_token}"}, timeout=10)
                    conn_resp.raise_for_status()
                    connections = conn_resp.json()
                    if connections:
                        tenant_id = connections[0].get("tenantId")
                except Exception as conn_err:
                    logger.warning("Could not dynamically resolve tenant: %s", conn_err)
                
                tenant_id = tenant_id or settings.xero_tenant_id
                if not tenant_id or tenant_id == "your_xero_tenant_id_here":
                    log(" Error: No authorized Xero organization found. Please go to the Connectors tab, click Connect Xero, and authorize access to your Xero organization.", "ERROR")
                    return
                xero_conn = XeroConnector(tenant_id=tenant_id, oauth_handler=oauth_handler)
                monday_conn = MondayConnector(api_key=monday_api_key)
                
                log(f" Querying items from Monday.com Invoice Board {inv_board_id}...")
                try:
                    board_items = monday_conn.query_board_items(board_id=inv_board_id, limit=batch_count)
                except Exception as e:
                    log(f" Failed to fetch items from Monday.com: {e}", "ERROR")
                    return
                
                log(f" Retrieved {len(board_items)} items from board. Transforming to Xero Invoices...")
                transformed_invoices = []
                validation_failures = []
                status_col_id = mappings_invoice.get("Invoice.Status") or "status"
                
                for item in board_items:
                    status_val = ""
                    for col in item.get("column_values", []):
                        if str(col.get("id", "")).lower() == status_col_id.lower():
                            status_val = str(col.get("text", "")).strip()
                    if status_val in ("Synced to Xero", "Paid"):
                        continue
                    
                    res = transformer.transform_record_to_invoice(
                        item,
                        mappings=mappings_invoice,
                        customer_mappings=mappings_customer,
                        product_mappings=mappings_product,
                    )
                    if res["status"] == "success":
                        transformed_invoices.append((item.get("id"), res["invoice"]))
                    else:
                        validation_failures.append(res)
                
                total_to_sync = len(transformed_invoices)
                log(f" Mapped {total_to_sync} pending invoices. Submitting to Xero API...")
                synced_count = 0
                failed_count = len(validation_failures)
                
                for idx, (monday_id, inv_model) in enumerate(transformed_invoices):
                    try:
                        # 1. Process/Sync Contact (Customer)
                        try:
                            xero_conn.create_or_update_contact(inv_model.Contact)
                        except Exception as c_err:
                            logger.warning("Contact sync failed (non-fatal, continuing): %s", c_err)
                            
                        # 2. Process/Ensure Products (Items) exist in Xero
                        for line in inv_model.LineItems:
                            if line.ItemCode:
                                try:
                                    from models.schemas import Product as SchemaProduct
                                    prod_model = SchemaProduct(
                                        Code=line.ItemCode,
                                        Name=line.description or line.ItemCode,
                                        UnitPrice=line.UnitAmount,
                                        Description=line.description
                                    )
                                    xero_conn.create_product_item(prod_model)
                                except Exception as p_err:
                                    logger.warning("Product sync warning: %s", p_err)
                                    
                        # 3. Process Invoice
                        xero_conn.create_invoice(inv_model)
                        monday_conn.update_item_status(monday_id, "Synced to Xero", board_id=inv_board_id)
                        monday_conn.post_item_update(monday_id, f" Successfully synced to Xero!\n• Invoice Number: {inv_model.invoice_number or 'INV-SUCCESS'}\n• Amount: £{inv_model.TotalAmount:.2f}")
                        synced_count += 1
                        log(f" Synced Invoice for {inv_model.Contact.Name} (£{inv_model.TotalAmount:.2f})")
                    except Exception as x_err:
                        monday_conn.update_item_status(monday_id, "Sync Error", board_id=inv_board_id)
                        monday_conn.post_item_update(monday_id, f" Xero Sync Error:\n{x_err}")
                        failed_count += 1
                        log(f" Failed to sync invoice for item {monday_id}: {x_err}", "ERROR")
                    time.sleep(0.1)
                
                total_execution_time = round(time.time() - start_time, 3)
                try:
                    log_entry = SyncLog(
                        timestamp=datetime.now(timezone.utc),
                        status="SUCCESS" if failed_count == 0 else "PARTIAL_SUCCESS",
                        direction=direction,
                        payload_count=synced_count + failed_count,
                        error_details=f"SPA Sync complete. Synced {synced_count} invoices from board {inv_board_id}. {failed_count} failures.",
                    )
                    db.add(log_entry)
                    db.commit()
                except Exception as db_err:
                    logger.warning("Could not persist SyncLog in SPA background: %s", db_err)
                log(f"[System Success] Sync execution complete! Synced {synced_count} invoices in {total_execution_time}s.")
    except Exception as e:
        log(f" Failed with error: {str(e)}", "ERROR")
    finally:
        db.close()


@router.get("/api/status")
async def get_api_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns connection flags for monday and xero."""
    user_id = getattr(current_user, "id", 1)
    
    monday_cred = get_user_credentials(db, user_id, "monday")
    xero_cred = get_user_credentials(db, user_id, "xero")
    
    monday_connected = True if (monday_cred and monday_cred.api_key) else False
    xero_connected = False
    if xero_cred and xero_cred.access_token:
        if xero_cred.token_expiry and time.time() > xero_cred.token_expiry:
            xero_connected = True if xero_cred.refresh_token else False
        else:
            xero_connected = True
            
    return {
        "monday_connected": monday_connected,
        "xero_connected": xero_connected
    }


@router.get("/api/logs")
async def get_spa_logs():
    """Returns the buffer of logs for the SPA console."""
    global SPA_LOGS
    return {"logs": SPA_LOGS}


@router.delete("/api/logs")
async def clear_spa_logs():
    """Clears the buffer of logs for the SPA console."""
    global SPA_LOGS
    SPA_LOGS.clear()
    return {"status": "success"}


@router.post("/api/sync-history/reset")
async def reset_sync_history(
    current_user: User = Depends(require_admin_or_higher),
    db: Session = Depends(get_db)
):
    """Deletes all sync log entries to reset the runtime cutoff filter."""
    try:
        db.query(SyncLog).delete()
        db.commit()
        return {"status": "success", "message": "Sync history successfully reset."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to reset sync history: {e}")


@router.post("/api/run-sync")
async def run_sync_from_spa(
    payload: dict,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    POST endpoint called by SPA dashboard to trigger a background sync task.
    """
    direction = payload.get("direction", "xero_to_monday")
    board_id_1 = payload.get("board_id_1")
    board_id_3 = payload.get("board_id_3") or "5101138235"
    batch_count = int(payload.get("batch_count", 100))
    group_by_company = bool(payload.get("group_by_company", True))
    target_account = str(payload.get("target_account", "200"))
    user_id = getattr(current_user, "id", 1)
    
    background_tasks.add_task(
        run_spa_sync_in_background,
        direction,
        board_id_1,
        board_id_3,
        batch_count,
        group_by_company,
        target_account,
        user_id
    )
    
    return Response(
        content=json.dumps({"status": "success", "message": "Sync pipeline started in background."}),
        status_code=202,
        media_type="application/json"
    )

