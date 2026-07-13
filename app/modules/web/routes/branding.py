from __future__ import annotations

from app.core.flask_config import BASE_DIR

BRAND_LOGO_DIR = BASE_DIR / "static" / "uploads" / "branding"

THEME_PRESETS = {
    "warm": {"primary": "#0f6a5d", "accent": "#b54a2f"},
    "classic": {"primary": "#2c5282", "accent": "#c05621"},
    "dark": {"primary": "#2d3748", "accent": "#ed8936"},
    "rose": {"primary": "#9b2c2c", "accent": "#d69e2e"},
    "ocean": {"primary": "#2b6cb0", "accent": "#38a169"},
}

