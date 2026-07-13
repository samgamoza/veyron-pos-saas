from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import session

from app.core.db import get_connection

BASE_DIR = Path(__file__).resolve().parent
TRANSLATIONS_DIR = BASE_DIR / "translations"
DEFAULT_LOCALE = "en"


class LocalizationService:
    # Display names for super-admin platform language picker (extend when adding `translations/*.json`).
    LOCALE_DISPLAY_NAMES: dict[str, str] = {
        "en": "English",
        "tl": "Filipino (Tagalog)",
        "es": "Spanish",
        "fr": "French",
        "zh": "Chinese",
    }

    @property
    def supported_locales(self) -> list[str]:
        found = sorted({path.stem for path in TRANSLATIONS_DIR.glob("*.json")})
        return found if found else [DEFAULT_LOCALE]

    @lru_cache(maxsize=16)
    def load_translations(self, locale: str) -> dict[str, str]:
        locale = self.normalize_locale(locale)
        translation_path = TRANSLATIONS_DIR / f"{locale}.json"
        if not translation_path.exists():
            translation_path = TRANSLATIONS_DIR / f"{DEFAULT_LOCALE}.json"
        try:
            return json.loads(translation_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def normalize_locale(self, locale: str | None) -> str:
        if not locale:
            return DEFAULT_LOCALE
        normalized = locale.strip().lower().split("-")[0]
        return normalized if normalized else DEFAULT_LOCALE

    def get_locale(self) -> str:
        language = session.get("language")
        if language:
            return self.normalize_locale(language)

        user_id = session.get("user_id")
        tenant_id = session.get("tenant_id")
        if user_id:
            with get_connection() as connection:
                user_row = connection.execute(
                    "SELECT language_override, tenant_id FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if user_row is not None and user_row["language_override"]:
                    return self.normalize_locale(user_row["language_override"])
                tenant_id = user_row["tenant_id"] or tenant_id

        if tenant_id:
            with get_connection() as connection:
                tenant_row = connection.execute(
                    "SELECT language FROM tenants WHERE id = ?",
                    (tenant_id,),
                ).fetchone()
                if tenant_row is not None and tenant_row["language"]:
                    return self.normalize_locale(tenant_row["language"])

        with get_connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE tenant_id = 1 AND key = ?",
                ("platform_default_language",),
            ).fetchone()
            if row is not None and row["value"]:
                return self.normalize_locale(row["value"])

        return DEFAULT_LOCALE

    def translate(self, key: str, default: str | None = None) -> str:
        translations = self.load_translations(self.get_locale())
        return translations.get(key, default if default is not None else key)

    def format_locale(self, locale: str | None) -> str:
        return self.normalize_locale(locale)
