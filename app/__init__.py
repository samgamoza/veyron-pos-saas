from __future__ import annotations

from flask import Flask

from app.core.flask_app import create_flask_application


def create_app() -> Flask:
    return create_flask_application()
