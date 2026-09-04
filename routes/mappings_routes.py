"""
M.I.R.A. Schema Mapping Management Router.
"""

from routes.mappings import (
    router,
    mappings_page,
    save_mappings,
    reset_mappings_to_default,
    delete_mapping_row,
    ensure_user_mappings,
)

__all__ = [
    "router",
    "mappings_page",
    "save_mappings",
    "reset_mappings_to_default",
    "delete_mapping_row",
    "ensure_user_mappings",
]
