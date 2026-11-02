# Vercel serverless entry point; the shared Handler in server.py does the routing.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import Handler as handler  # noqa: E402,F401
