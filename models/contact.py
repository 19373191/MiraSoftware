"""
M.I.R.A. Contact and Account Data Schemas.
"""

import re
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class ContactType(str, Enum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    LEAD = "LEAD"


class ContactStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    PENDING = "PENDING"


class Contact(BaseModel):
    """Schema representing a customer or supplier contact."""

    contact_id: Optional[str] = Field(None, description="Unique contact identifier")
    name: str = Field(..., description="Full company or contact name")
    first_name: Optional[str] = Field(None, description="First name of primary contact person")
    last_name: Optional[str] = Field(None, description="Last name of primary contact person")
    email_address: str = Field(..., description="Primary contact email address")
    phone: Optional[str] = Field(None, description="Contact phone number")
    contact_type: ContactType = Field(default=ContactType.CUSTOMER, description="Type of contact")
    status: ContactStatus = Field(default=ContactStatus.ACTIVE, description="Contact status")
    monday_item_id: Optional[str] = Field(None, description="Associated Monday.com item ID")
    xero_contact_id: Optional[str] = Field(None, description="Associated Xero contact ID")

    @field_validator("email_address")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        """Validates email format strictly."""
        if not value or not EMAIL_REGEX.match(value.strip()):
            raise ValueError(f"Invalid email address format: '{value}'")
        return value.strip()


class Account(BaseModel):
    """Schema representing a chart of accounts entry or enterprise account."""

    account_id: Optional[str] = Field(None, description="Account unique identifier")
    code: str = Field(..., description="Chart of account code e.g. 200")
    name: str = Field(..., description="Account display name")
    account_type: str = Field(default="SALES", description="Account classification type")
    tax_type: Optional[str] = Field(None, description="Tax type classification e.g. OUTPUT2")
    contacts: List[Contact] = Field(default_factory=list, description="Associated contacts")
