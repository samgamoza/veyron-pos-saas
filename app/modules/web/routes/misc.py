from __future__ import annotations

from flask import Flask

from app.core.flask_config import APP_ENV



def register_misc_routes(app: Flask) -> None:
    @app.get("/healthz")
    def healthcheck() -> tuple[dict[str, object], int]:
        return {
            "status": "ok",
            "app_env": APP_ENV,
            "database_engine": "sqlite",
            "remote_ready": True,
        }, 200


    @app.route("/debug/routes")
    def debug_routes() -> tuple[dict[str, object], int]:
        return {
            "routes": sorted(str(rule) for rule in app.url_map.iter_rules()),
        }, 200


