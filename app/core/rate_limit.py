"""Shared rate limiter.

Opt-in per endpoint (``default_limits=[]``) so high-frequency POS and API traffic
is never throttled — only sensitive auth endpoints declare explicit limits. Uses
in-memory storage by default; point ``RATELIMIT_STORAGE_URI`` at Redis
(e.g. ``redis://host:6379``) when running multiple gunicorn workers so limits are
shared across processes.
"""

from __future__ import annotations

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    strategy="fixed-window",
)
