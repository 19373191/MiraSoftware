"""
M.I.R.A. Authentication & Super-Admin Seeding Router.
"""

from routes.auth import router, seed_super_admin, login_page, login_submit, logout

__all__ = ["router", "seed_super_admin", "login_page", "login_submit", "logout"]
