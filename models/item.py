"""
M.I.R.A. Inventory Item Data Schemas.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ItemCategory(str, Enum):
    SERVICES = "SERVICES"
    PRODUCTS = "PRODUCTS"
    SUBSCRIPTION = "SUBSCRIPTION"


class ItemStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISCONTINUED = "DISCONTINUED"


class Item(BaseModel):
    """Schema representing an inventory or product item."""
    item_id: Optional[str] = Field(None, description="Unique item identifier")
    code: str = Field(..., description="Item SKU or code")
    name: str = Field(..., description="Item name")
    description: Optional[str] = Field(None, description="Detailed item description")
    unit_price: float = Field(..., gt=0, description="Unit price for the item")
    category: ItemCategory = Field(default=ItemCategory.SERVICES, description="Product/Service category")
    status: ItemStatus = Field(default=ItemStatus.ACTIVE, description="Item availability status")
    monday_item_id: Optional[str] = Field(None, description="Associated Monday.com item ID")
    xero_item_id: Optional[str] = Field(None, description="Associated Xero item ID")
