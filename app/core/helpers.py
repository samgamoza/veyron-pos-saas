from __future__ import annotations

from typing import Any


def peso(value: float | int | None, currency_code: str = "PHP") -> str:
    amount = float(value or 0)
    return f"{currency_code} {amount:,.2f}"


def normalize_lookup_name(raw_name: str) -> str:
    return " ".join(part for part in raw_name.strip().split() if part)


def ensure_string(value: Any, fallback: str = "") -> str:
    return str(value or fallback).strip()
