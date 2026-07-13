from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from typing import Any


def _load_fernet():
    try:
        module = importlib.import_module("cryptography.fernet")
        return getattr(module, "Fernet")
    except (ImportError, ModuleNotFoundError):
        return None


Fernet = _load_fernet()


class CredentialEncryptionError(RuntimeError):
    pass


def _get_secret_key() -> bytes:
    secret = os.getenv("PAYMENT_CREDENTIALS_SECRET") or os.getenv("SECRET_KEY")
    if not secret:
        raise CredentialEncryptionError("PAYMENT_CREDENTIALS_SECRET or SECRET_KEY must be set to secure payment credentials.")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _encrypt_value(value: str) -> str:
    data = value.encode("utf-8")
    if Fernet is not None:
        key = base64.urlsafe_b64encode(_get_secret_key())
        return Fernet(key).encrypt(data).decode("utf-8")

    key = _get_secret_key()
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(xored).decode("utf-8")


def _decrypt_value(value: str) -> str:
    if Fernet is not None:
        key = base64.urlsafe_b64encode(_get_secret_key())
        return Fernet(key).decrypt(value.encode("utf-8")).decode("utf-8")

    raw = base64.urlsafe_b64decode(value.encode("utf-8"))
    key = _get_secret_key()
    plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return plain.decode("utf-8")


class PaymentCredentialStore:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def save_credentials(self, tenant_id: int, gateway_name: str, config: dict[str, Any]) -> None:
        encrypted = _encrypt_value(json.dumps(config))
        self.connection.execute(
            """
            INSERT INTO payment_credentials (tenant_id, gateway_name, encrypted_config, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id, gateway_name) DO UPDATE SET encrypted_config = excluded.encrypted_config, updated_at = excluded.updated_at
            """,
            (tenant_id, gateway_name, encrypted,)
        )

    def load_credentials(self, tenant_id: int, gateway_name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT encrypted_config FROM payment_credentials WHERE tenant_id = ? AND gateway_name = ?",
            (tenant_id, gateway_name),
        ).fetchone()
        if row is None:
            return None
        return json.loads(_decrypt_value(row["encrypted_config"]))
