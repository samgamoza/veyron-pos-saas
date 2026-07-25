from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, request, send_file, session, url_for

from app.core.auth import login_required
from app.core.constants import ALLOWED_IMAGE_EXTENSIONS
from app.core.db import get_connection
from app.core.locations import LocationService
from app.core.promotions import PromotionError, PromotionService
from app.core.flask_config import BACKUP_DIR, DATABASE, DATABASE_ENGINE, DEFAULT_APP_SETTINGS
from app.modules.users.decorators import roles_required
from app.modules.web import queries as web_queries
from app.modules.web.routes.branding import BRAND_LOGO_DIR, THEME_PRESETS



def register_owner_routes(app: Flask) -> None:
    @app.route("/owner/branches/add", methods=["POST"])
    @login_required("owner")
    def add_branch():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant context.", "error")
            return redirect(url_for("owner_dashboard"))
        try:
            with get_connection() as connection:
                svc = LocationService(connection)
                loc_id = svc.create_location(
                    int(tenant_id),
                    request.form.get("name", ""),
                    code=request.form.get("code", ""),
                    address=request.form.get("address", ""),
                    phone=request.form.get("phone", ""),
                    is_default=bool(request.form.get("is_default")),
                )
                web_queries.log_audit(connection, "create", "location", loc_id, f"Branch added: {request.form.get('name','').strip()}")
            flash("Branch added.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/branches/set-default", methods=["POST"])
    @login_required("owner")
    def set_default_branch():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant context.", "error")
            return redirect(url_for("owner_dashboard"))
        try:
            with get_connection() as connection:
                LocationService(connection).set_default(int(tenant_id), int(request.form.get("location_id", 0)))
            flash("Default branch updated.", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/branches/toggle-active", methods=["POST"])
    @login_required("owner")
    def toggle_branch_active():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant context.", "error")
            return redirect(url_for("owner_dashboard"))
        try:
            with get_connection() as connection:
                svc = LocationService(connection)
                loc_id = int(request.form.get("location_id", 0))
                current = svc.get_location(int(tenant_id), loc_id)
                if current is None:
                    raise ValueError("Branch not found.")
                svc.update_location(int(tenant_id), loc_id, is_active=not current["is_active"])
            flash("Branch updated.", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/promotions/add", methods=["POST"])
    @login_required("owner")
    def add_promotion():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant context.", "error")
            return redirect(url_for("owner_dashboard"))
        try:
            with get_connection() as connection:
                pid = PromotionService(connection).create_promotion(
                    int(tenant_id),
                    request.form.get("name", ""),
                    request.form.get("code", ""),
                    discount_type=request.form.get("discount_type", "percent"),
                    value=float(request.form.get("value", 0) or 0),
                    min_subtotal=float(request.form.get("min_subtotal", 0) or 0),
                    usage_limit=int(request.form["usage_limit"]) if request.form.get("usage_limit") else None,
                )
                web_queries.log_audit(connection, "create", "promotion", pid, f"Promo added: {request.form.get('code','').strip()}")
            flash("Promotion added.", "success")
        except (PromotionError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/promotions/toggle", methods=["POST"])
    @login_required("owner")
    def toggle_promotion():
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant context.", "error")
            return redirect(url_for("owner_dashboard"))
        try:
            with get_connection() as connection:
                svc = PromotionService(connection)
                promo_id = int(request.form.get("promotion_id", 0))
                activate = request.form.get("activate") == "1"
                svc.set_active(int(tenant_id), promo_id, activate)
            flash("Promotion updated.", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("owner_dashboard"))

    @app.route("/owner/backups/create", methods=["POST"])
    @login_required("owner")
    def create_backup():
        backup_name = f"veyron-pos-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        backup_path = BACKUP_DIR / backup_name
        try:
            web_queries.copy_database(backup_path)
        except RuntimeError as error:
            flash(str(error), "error")
            return redirect(url_for("owner_dashboard"))
        with get_connection() as connection:
            web_queries.log_audit(connection, "backup", "database", None, f"Backup created: {backup_name}")
        flash("Backup created.", "success")
        return redirect(url_for("owner_dashboard"))


    @app.route("/owner/backups/download/<path:backup_name>")
    @login_required("owner")
    def download_backup(backup_name: str):
        backup_path = BACKUP_DIR / Path(backup_name).name
        if not backup_path.exists():
            flash("Backup not found.", "error")
            return redirect(url_for("owner_dashboard"))
        return send_file(backup_path, as_attachment=True, download_name=backup_path.name)


    @app.route("/owner/backups/restore", methods=["POST"])
    @login_required("owner")
    def restore_backup():
        if DATABASE_ENGINE == "postgres":
            flash("Restore is only available for SQLite backups. Use managed PostgreSQL restore tooling in production.", "error")
            return redirect(url_for("owner_dashboard"))

        backup_name = request.form.get("backup_name", "").strip()
        backup_path = BACKUP_DIR / Path(backup_name).name
        if not backup_path.exists():
            flash("Backup not found.", "error")
            return redirect(url_for("owner_dashboard"))

        safety_name = f"pre-restore-{backup_path.name}"
        web_queries.copy_database(BACKUP_DIR / safety_name)
        shutil.copy2(backup_path, DATABASE)
        with get_connection() as connection:
            web_queries.log_audit(connection, "restore", "database", None, f"Database restored from {backup_path.name}")
        flash("Backup restored.", "success")
        return redirect(url_for("owner_dashboard"))


    @app.route("/owner/settings/save", methods=["POST"])
    @login_required("owner")
    def save_settings():
        delivery_enabled = 1 if request.form.get("delivery_enabled") else 0
        # VAT rate is entered as a percentage in the UI and stored as a decimal.
        raw_vat_percent = request.form.get("vat_rate_percent", "").strip()
        try:
            vat_rate_value = f"{max(0.0, min(100.0, float(raw_vat_percent))) / 100:.4f}"
        except ValueError:
            vat_rate_value = DEFAULT_APP_SETTINGS["vat_rate"]

        def _clean_rate(field: str, default: str) -> str:
            try:
                return f"{max(0.0, float(request.form.get(field, default))):.4f}"
            except ValueError:
                return default

        settings = {
            "vat_rate": vat_rate_value,
            "vat_inclusive": "1" if request.form.get("vat_inclusive") else "0",
            "vat_registered": "1" if request.form.get("vat_registered") else "0",
            "loyalty_enabled": "1" if request.form.get("loyalty_enabled") else "0",
            "loyalty_earn_rate": _clean_rate("loyalty_earn_rate", DEFAULT_APP_SETTINGS["loyalty_earn_rate"]),
            "loyalty_redeem_rate": _clean_rate("loyalty_redeem_rate", DEFAULT_APP_SETTINGS["loyalty_redeem_rate"]),
            "auto_print_receipt": "1" if request.form.get("auto_print_receipt") else "0",
            "cash_drawer_enabled": "1" if request.form.get("cash_drawer_enabled") else "0",
            "printer_mode": request.form.get("printer_mode", "browser").strip() or "browser",
            "printer_bridge_url": request.form.get("printer_bridge_url", "").strip()
            or DEFAULT_APP_SETTINGS["printer_bridge_url"],
            "drawer_open_note": request.form.get("drawer_open_note", "").strip() or DEFAULT_APP_SETTINGS["drawer_open_note"],
            "alert_low_stock_email": "1" if request.form.get("alert_low_stock_email") else "0",
            "alert_void_refund_email": "1" if request.form.get("alert_void_refund_email") else "0",
            "alert_variance_email": "1" if request.form.get("alert_variance_email") else "0",
            "delivery_fee_base": request.form.get("delivery_fee_base", "0").strip() or "0.00",
            "delivery_fee_per_km": request.form.get("delivery_fee_per_km", "0").strip() or "0.00",
            "delivery_fee_free_threshold": request.form.get("delivery_fee_free_threshold", "0").strip() or "0.00",
            "delivery_zones": request.form.get("delivery_zones", "").strip(),
        }
        tenant_id = session.get("tenant_id")
        with get_connection() as connection:
            if tenant_id:
                connection.execute("UPDATE tenants SET delivery_enabled = ? WHERE id = ?", (delivery_enabled, tenant_id))
            if tenant_id:
                for key, value in settings.items():
                    connection.execute(
                        """
                        INSERT INTO app_settings (tenant_id, key, value) VALUES (?, ?, ?)
                        ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                        """,
                        (tenant_id, key, value),
                    )
            web_queries.log_audit(connection, "update", "settings", None, "Hardware, receipt, and delivery settings updated")
        flash("Settings saved.", "success")
        return redirect(url_for("owner_dashboard"))


    @app.route("/owner/branding/save", methods=["POST"])
    @login_required("owner")
    def save_branding():
        import re

        primary = request.form.get("brand_primary_color", "#0f6a5d").strip()
        accent = request.form.get("brand_accent_color", "#b54a2f").strip()
        theme = request.form.get("brand_theme_mode", "warm").strip()

        hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        if not hex_re.match(primary):
            primary = "#0f6a5d"
        if not hex_re.match(accent):
            accent = "#b54a2f"
        if theme not in THEME_PRESETS and theme != "custom":
            theme = "warm"

        branding = {
            "brand_primary_color": primary,
            "brand_accent_color": accent,
            "brand_theme_mode": theme,
        }

        logo_file = request.files.get("brand_logo")
        if logo_file and logo_file.filename:
            from werkzeug.utils import secure_filename

            filename = secure_filename(logo_file.filename)
            ext = Path(filename).suffix.lower()
            if ext in ALLOWED_IMAGE_EXTENSIONS:
                BRAND_LOGO_DIR.mkdir(parents=True, exist_ok=True)
                if ext == ".pdf":
                    import fitz
                    pdf_bytes = logo_file.read()
                    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = doc[0]
                    pix = page.get_pixmap(dpi=300)
                    save_path = BRAND_LOGO_DIR / "logo.png"
                    pix.save(str(save_path))
                    doc.close()
                    branding["brand_logo_path"] = "uploads/branding/logo.png"
                else:
                    save_path = BRAND_LOGO_DIR / f"logo{ext}"
                    logo_file.save(save_path)
                    branding["brand_logo_path"] = f"uploads/branding/logo{ext}"

        tenant_id_brand = session.get("tenant_id")
        with get_connection() as connection:
            if tenant_id_brand:
                for key, value in branding.items():
                    connection.execute(
                        """
                        INSERT INTO app_settings (tenant_id, key, value) VALUES (?, ?, ?)
                        ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value
                        """,
                        (tenant_id_brand, key, value),
                    )
                web_queries.log_audit(connection, "update", "branding", None, f"Branding updated: theme={theme}")
        flash("Branding updated.", "success")
        return redirect(url_for("owner_dashboard"))


    @app.route("/owner/hardware/drawer", methods=["POST"])
    @roles_required("tenant_admin")
    def open_cash_drawer_hook():
        with get_connection() as connection:
            web_queries.log_audit(connection, "drawer_open", "hardware", None, "Cash drawer open requested from receipt screen")
        flash("Drawer open logged. Use a local printer bridge for actual ESC/POS drawer pulses.", "success")
        return redirect(request.form.get("return_to") or url_for("pos"))
