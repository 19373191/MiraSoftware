"""
M.I.R.A. Domain Schemas.

Pydantic V2 models for Contact/Account, Product/Item, LineItem, and Invoice
matching Xero integration specifications.
"""

import re
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class Contact(BaseModel):
    """Schema representing a Xero Contact or Account."""

    name: str = Field(..., alias="Name", description="Company or contact name")
    email: str = Field(..., alias="Email", description="Contact email address")
    first_name: Optional[str] = Field(None, alias="FirstName", description="Contact person first name")
    last_name: Optional[str] = Field(None, alias="LastName", description="Contact person last name")
    phone: Optional[str] = Field(None, alias="Phone", description="Contact phone number")
    account_number: Optional[str] = Field(None, alias="AccountNumber", description="Account number")
    contact_id: Optional[str] = Field(None, alias="ContactID", description="Contact ID")
    address: Optional[str] = Field(None, alias="Address", description="Contact address")

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        """Validates email format strictly."""
        if not value or not EMAIL_REGEX.match(value.strip()):
            raise ValueError(f"Invalid email address format: '{value}'")
        return value.strip()

    @property
    def Name(self) -> str:
        return self.name

    @property
    def Email(self) -> str:
        return self.email

    @property
    def FirstName(self) -> Optional[str]:
        return self.first_name

    @property
    def LastName(self) -> Optional[str]:
        return self.last_name

    @property
    def Phone(self) -> Optional[str]:
        return self.phone

    @property
    def AccountNumber(self) -> Optional[str]:
        return self.account_number

    @property
    def ContactID(self) -> Optional[str]:
        return self.contact_id

    @property
    def Address(self) -> Optional[str]:
        return self.address


# Alias Account to Contact for domain terminology compatibility
Account = Contact


class Product(BaseModel):
    """Schema representing a Product or Catalog Item."""

    code: str = Field(..., alias="Code", description="Product SKU or code")
    name: str = Field(..., alias="Name", description="Product title")
    unit_price: float = Field(..., alias="UnitPrice", gt=0, description="Unit price per item (> 0)")
    description: Optional[str] = Field(None, alias="Description", description="Item description")

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    @property
    def Code(self) -> str:
        return self.code

    @property
    def Name(self) -> str:
        return self.name

    @property
    def UnitPrice(self) -> float:
        return self.unit_price

    @property
    def Description(self) -> Optional[str]:
        return self.description


# Alias Item to Product for domain terminology compatibility
Item = Product


class LineItem(BaseModel):
    """Schema representing an individual line item on a Xero invoice."""

    item_code: str = Field(..., alias="ItemCode", description="Product SKU or item code")
    quantity: float = Field(..., alias="Quantity", gt=0, description="Quantity (> 0)")
    unit_amount: float = Field(..., alias="UnitAmount", gt=0, description="Unit price (> 0)")
    tax_type: Optional[str] = Field("OUTPUT2", alias="TaxType", description="Xero tax classification rate")
    description: Optional[str] = Field(None, alias="Description", description="Line item description")

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    @property
    def ItemCode(self) -> str:
        return self.item_code

    @property
    def Quantity(self) -> float:
        return self.quantity

    @property
    def UnitAmount(self) -> float:
        return self.unit_amount

    @property
    def TaxType(self) -> Optional[str]:
        return self.tax_type

    @property
    def Description(self) -> Optional[str]:
        return self.description

    @property
    def LineAmount(self) -> float:
        """Calculates total line item cost before tax."""
        return round(self.quantity * self.unit_amount, 2)


class Invoice(BaseModel):
    """Schema representing a complete Xero Invoice."""

    contact: Contact = Field(..., alias="Contact", description="Associated Xero Contact/Account")
    line_items: List[LineItem] = Field(..., alias="LineItems", min_items=1, description="Invoice line items list")
    issue_date: str = Field(..., alias="IssueDate", description="Invoice issue date (YYYY-MM-DD)")
    due_date: str = Field(..., alias="DueDate", description="Invoice due date (YYYY-MM-DD)")
    status: str = Field("DRAFT", alias="Status", description="Xero invoice status (DRAFT, AUTHORISED, etc.)")

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    @property
    def Contact(self) -> Contact:
        return self.contact

    @property
    def LineItems(self) -> List[LineItem]:
        return self.line_items

    @property
    def IssueDate(self) -> str:
        return self.issue_date

    @property
    def DueDate(self) -> str:
        return self.due_date

    @property
    def Status(self) -> str:
        return self.status

    @property
    def TotalAmount(self) -> float:
        """Calculates total invoice amount across all line items."""
        return round(sum(item.LineAmount for item in self.line_items), 2)
