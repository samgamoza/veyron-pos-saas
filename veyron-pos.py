"""Application entrypoint. Prefer ``from app import create_app`` for WSGI and tests."""

from __future__ import annotations

import os

from app.core.flask_app import create_flask_application

app = create_flask_application()

if __name__ == "__main__":
    _env = os.getenv("APP_ENV", "development").strip().lower()
    _debug = os.getenv("FLASK_DEBUG", "1" if _env != "production" else "0") == "1"
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000"))),
        debug=_debug,
    )
