from __future__ import annotations

from typing import Any

from flask import Response, jsonify


def ok_json(data: Any | None = None, message: str | None = None, status: int = 200) -> tuple[Response, int]:
    body: dict[str, Any] = {"ok": True}
    if data is not None:
        body["data"] = data
    if message:
        body["message"] = message
    return jsonify(body), status


def err_json(
    message: str,
    *,
    code: str = "error",
    status: int = 400,
    details: Any | None = None,
) -> tuple[Response, int]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return jsonify({"ok": False, "error": err}), status
