"""Shared DB integrity error tuple for `except` clauses (avoids circular imports)."""

from __future__ import annotations

import sqlite3

from app.core.db import PostgresIntegrityError

DB_INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError, PostgresIntegrityError)
