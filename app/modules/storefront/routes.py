from __future__ import annotations

from flask import Blueprint, abort, g, request, session

from app.core.localization import localization_service
from app.modules.storefront.storefront_service import StorefrontService

storefront_bp = Blueprint("storefront", __name__)
storefront_service = StorefrontService()


@storefront_bp.route("/storefront")
@storefront_bp.route("/store")
def storefront() -> str:
    if g.tenant is None:
        abort(404)

    lang_raw = request.args.get("lang", "").strip()
    if lang_raw:
        lang = localization_service.normalize_locale(lang_raw)
        if lang in localization_service.supported_locales:
            session["language"] = lang

    return storefront_service.render_storefront(g.tenant.id, request.args.get("theme", ""))
