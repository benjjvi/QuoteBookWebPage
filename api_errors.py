from __future__ import annotations

from typing import Any

from flask import jsonify


def build_error_payload(
    *,
    code: str,
    message: str,
    details: Any = None,
) -> dict[str, Any]:
    payload = {
        "code": str(code).strip() or "unknown_error",
        "message": str(message).strip() or "Unknown error.",
        "details": details if details is not None else {},
    }
    # Backward-compatible alias for older clients that still read "error".
    payload["error"] = payload["message"]
    return payload


def error_response(
    *,
    status: int,
    code: str,
    message: str,
    details: Any = None,
):
    return jsonify(build_error_payload(code=code, message=message, details=details)), int(
        status
    )
