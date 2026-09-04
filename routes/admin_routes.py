"""
M.I.R.A. Admin User Management Router.
"""

from routes.admin import (
    router,
    admin_list_users,
    admin_create_user,
    admin_toggle_user_active,
)

__all__ = [
    "router",
    "admin_list_users",
    "admin_create_user",
    "admin_toggle_user_active",
]
