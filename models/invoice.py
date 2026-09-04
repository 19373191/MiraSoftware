"""
M.I.R.A. Invoice Data Schemas.
"""

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from models.contact import Contact


class InvoiceType(str, Enum):
    ACCREC = "ACCREC"  # Accounts Receivable (Sales Invoice)
    ACCPAY = "ACCPAY"  # Accounts Payable (Bills)


class InvoiceStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    AUTHORISED = "AUTHORISED"
    PAID = "PAID"
    VOIDED = "VOIDED"


class InvoiceLineItem(BaseModel):
    """Schema representing an individual line item on an invoice."""
    model_config = ConfigDict(arbitrary_types_allowed=True, ignored_types=(property,))
    description: str = Field(..., description="Description of product or service")
    quantity: int = Field(..., gt=0, description="Quantity of items")
    unit_amount: float = Field(..., gt=0, description="Unit price per item")
    account_code: str = Field(default="200", description="Chart of accounts code")
    item_code: Optional[str] = Field(None, description="Product code or SKU")
    tax_amount: Optional[float] = Field(0.0, description="Calculated line item tax")

    @property
    def line_total(self) -> float:
        """Calculates total line item cost."""
        return self.quantity * self.unit_amount


class InvoiceCreate(BaseModel):
    """Schema for creating a new invoice."""
    type: InvoiceType = Field(default=InvoiceType.ACCREC, description="Invoice type")
    contact: Contact = Field(..., description="Associated customer or entity contact")
    date: str = Field(..., description="Invoice issue date (YYYY-MM-DD)")
    due_date: str = Field(..., description="Invoice due date (YYYY-MM-DD)")
    reference: Optional[str] = Field(None, description="External transaction reference ID")
    line_items: List[InvoiceLineItem] = Field(..., min_length=1, description="List of line items")
    status: InvoiceStatus = Field(default=InvoiceStatus.DRAFT, description="Target invoice status")


class Invoice(InvoiceCreate):
    """Schema representing a complete invoice record."""
    invoice_id: Optional[str] = Field(None, description="Unique invoice ID assigned by system or Xero")
    total_tax: Optional[float] = Field(0.0, description="Total tax amount")
    total_amount: Optional[float] = Field(0.0, description="Total invoice amount")
    monday_item_id: Optional[str] = Field(None, description="Monday.com item ID")
    xero_invoice_id: Optional[str] = Field(None, description="Xero invoice ID")


class InvoiceBatchResponse(BaseModel):
    """Batch operation outcome response."""
    status: str = Field(..., description="Overall batch process status")
    transformed_invoices: List[Any] = Field(default_factory=list, description="List of transformed invoice objects")
    failed_records: List[dict] = Field(default_factory=list, description="List of failed records and validation errors")
