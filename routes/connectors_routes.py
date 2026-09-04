"""
M.I.R.A. Platform Connector Management Router.
"""

from routes.connectors import (
    router,
    connectors_page,
    save_monday_credentials,
    test_monday_connection,
    xero_oauth_connect,
    xero_oauth_callback,
    disconnect_xero,
)

__all__ = [
    "router",
    "connectors_page",
    "save_monday_credentials",
    "test_monday_connection",
    "xero_oauth_connect",
    "xero_oauth_callback",
    "disconnect_xero",
]
