"""
Example PythonAnywhere WSGI entrypoint for QuoteBook.

Usage on PythonAnywhere:
1. Copy this file's contents into your WSGI configuration file
   (usually /var/www/<username>_pythonanywhere_com_wsgi.py), or import it.
2. Update QUOTEBOOK_PROJECT_ROOT below (or set it as an env var in the WSGI file).
3. Reload the web app from the PythonAnywhere dashboard.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Set your project path here if you do not provide QUOTEBOOK_PROJECT_ROOT.
DEFAULT_PROJECT_ROOT = (
    "/home/you/qb"  # <-- UPDATE THIS PATH TO YOUR PROJECT ROOT IF NOT USING ENV VAR
)

PROJECT_ROOT = os.getenv("QUOTEBOOK_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)
if not os.path.isdir(PROJECT_ROOT):
    # Fallback to this file's directory when used directly inside project root.
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load env vars from .env if present.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Choose which Flask app to expose.
# - "client" => app.py (main website)
# - "api"    => api_server.py (standalone quote API)
WSGI_TARGET = os.getenv("WSGI_TARGET", "client").strip().lower()

if WSGI_TARGET == "api":
    from api_server import app as application  # noqa: E402
else:
    from app import app as application  # noqa: E402
