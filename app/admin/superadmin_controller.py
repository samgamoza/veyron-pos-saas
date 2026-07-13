from __future__ import annotations

import json
from functools import wraps

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for

from app.core.helpers import normalize_lookup_name
from app.core.localization import localization_service
from .service import AdminService

superadmin_bp = Blueprint("superadmin", __name__, url_prefix="/superadmin")
admin_service = AdminService()


def _superadmin_dashboard_with_tab(tab: str) -> str:
    return f"{url_for('superadmin.super_admin_dashboard')}#{tab}"


def super_admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id") or not session.get("is_super_admin"):
            flash("Super admin access required.", "error")
            return redirect(url_for("superadmin_login"))
        return view(*args, **kwargs)

    return wrapped_view


def require_recent_reauth():
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


@superadmin_bp.route("/", endpoint="super_admin_dashboard")
@super_admin_required
def dashboard() -> str:
    context = admin_service.fetch_super_admin_context()
    return render_template("super_admin_dashboard.html", **context)


@superadmin_bp.route("/tenants/add", methods=["POST"], endpoint="add_tenant")
@super_admin_required
@require_recent_reauth()
def add_tenant() -> str:
    tenant_name = normalize_lookup_name(request.form.get("tenant_name", ""))
    plan_name = request.form.get("plan_name", "starter").strip().lower() or "starter"
    subscription_status = request.form.get("subscription_status", "trial").strip().lower() or "trial"
    contact_email = request.form.get("contact_email", "").strip()
    try:
        monthly_fee = round(float(request.form.get("monthly_fee", "0") or 0), 2)
    except ValueError:
        monthly_fee = 0.0
    if not tenant_name:
        flash("Tenant name is required.", "error")
        return redirect(url_for("superadmin.super_admin_dashboard"))

    language = request.form.get("language", "en").strip().lower() or "en"
    billing_currency = request.form.get("billing_currency", "PHP").strip().upper() or "PHP"
    billing_cycle = request.form.get("billing_cycle", "monthly").strip().lower() or "monthly"
    payment_gateway = request.form.get("payment_gateway", "").strip()
    payment_gateway_mode = request.form.get("payment_gateway_mode", "test").strip().lower() or "test"
    delivery_enabled = 1 if request.form.get("delivery_enabled") == "1" else 0
    delivery_provider = request.form.get("delivery_provider", "").strip()
    storefront_enabled = 1 if request.form.get("storefront_enabled") == "1" else 0
    storefront_url = request.form.get("storefront_url", "").strip()
    auto_storefront_enabled = 1 if request.form.get("auto_storefront_enabled") == "1" else 0
    pos_profile = request.form.get("pos_profile", "cafe_bakery").strip().lower() or "cafe_bakery"

    try:
        admin_service.create_tenant(
            name=tenant_name,
            plan_name=plan_name,
            subscription_status=subscription_status,
            monthly_fee=monthly_fee,
            contact_email=contact_email,
            language=language,
            billing_currency=billing_currency,
            billing_cycle=billing_cycle,
            payment_gateway=payment_gateway,
            payment_gateway_mode=payment_gateway_mode,
            delivery_enabled=delivery_enabled,
            delivery_provider=delivery_provider,
            storefront_enabled=storefront_enabled,
            storefront_url=storefront_url,
            auto_storefront_enabled=auto_storefront_enabled,
            pos_profile=pos_profile,
        )
        flash("Tenant added.", "success")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("superadmin.super_admin_dashboard"))


@superadmin_bp.route("/tenants/edit", methods=["POST"], endpoint="edit_tenant")
@super_admin_required
@require_recent_reauth()
def edit_tenant() -> str:
    tenant_id = request.form.get("tenant_id")
    tenant_name = normalize_lookup_name(request.form.get("tenant_name", ""))
    is_active = request.form.get("is_active")
    plan_name = request.form.get("plan_name", "starter").strip().lower() or "starter"
    subscription_status = request.form.get("subscription_status", "trial").strip().lower() or "trial"
    contact_email = request.form.get("contact_email", "").strip()
    try:
        monthly_fee = round(float(request.form.get("monthly_fee", "0") or 0), 2)
    except ValueError:
        monthly_fee = 0.0
    if not tenant_id or not tenant_name or is_active not in ("0", "1"):
        flash("All fields are required.", "error")
        return redirect(url_for("superadmin.super_admin_dashboard"))

    language = request.form.get("language", "en").strip().lower() or "en"
    billing_currency = request.form.get("billing_currency", "PHP").strip().upper() or "PHP"
    billing_cycle = request.form.get("billing_cycle", "monthly").strip().lower() or "monthly"
    payment_gateway = request.form.get("payment_gateway", "").strip()
    payment_gateway_mode = request.form.get("payment_gateway_mode", "test").strip().lower() or "test"
    delivery_enabled = 1 if request.form.get("delivery_enabled") == "1" else 0
    delivery_provider = request.form.get("delivery_provider", "").strip()
    delivery_api_key = request.form.get("delivery_api_key", "").strip()
    delivery_callback_url = request.form.get("delivery_callback_url", "").strip()
    storefront_enabled = 1 if request.form.get("storefront_enabled") == "1" else 0
    storefront_url = request.form.get("storefront_url", "").strip()
    auto_storefront_enabled = 1 if request.form.get("auto_storefront_enabled") == "1" else 0
    next_billing_date = request.form.get("next_billing_date", "").strip() or None
    last_payment_date = request.form.get("last_payment_date", "").strip() or None

    parent_raw = request.form.get("parent_tenant_id", "").strip()
    parent_tenant_id: int | None = None
    if parent_raw:
        try:
            parent_tenant_id = int(parent_raw)
        except ValueError:
            parent_tenant_id = None
    org_slug = request.form.get("org_slug", "").strip()
    outlet_code = request.form.get("outlet_code", "").strip()
    bir_tin = request.form.get("bir_tin", "").strip()
    bir_vat_registered = 1 if request.form.get("bir_vat_registered") == "1" else 0
    compliance_notes = request.form.get("compliance_notes", "").strip()
    feature_flags = {
        "api": 1 if request.form.get("ff_api") == "1" else 0,
        "pos_advanced": 1 if request.form.get("ff_pos_advanced") == "1" else 0,
        "multi_branch": 1 if request.form.get("ff_multi_branch") == "1" else 0,
        "bir_exports": 1 if request.form.get("ff_bir_exports") == "1" else 0,
    }
    feature_flags_json = json.dumps(feature_flags)
    pos_profile = request.form.get("pos_profile", "cafe_bakery").strip().lower() or "cafe_bakery"

    try:
        admin_service.update_tenant(
            tenant_id=int(tenant_id),
            name=tenant_name,
            is_active=int(is_active),
            plan_name=plan_name,
            subscription_status=subscription_status,
            monthly_fee=monthly_fee,
            contact_email=contact_email,
            language=language,
            billing_currency=billing_currency,
            billing_cycle=billing_cycle,
            payment_gateway=payment_gateway,
            payment_gateway_mode=payment_gateway_mode,
            delivery_enabled=delivery_enabled,
            delivery_provider=delivery_provider,
            delivery_api_key=delivery_api_key,
            delivery_callback_url=delivery_callback_url,
            storefront_enabled=storefront_enabled,
            storefront_url=storefront_url,
            auto_storefront_enabled=auto_storefront_enabled,
            next_billing_date=next_billing_date,
            last_payment_date=last_payment_date,
            parent_tenant_id=parent_tenant_id,
            org_slug=org_slug,
            outlet_code=outlet_code,
            bir_tin=bir_tin,
            bir_vat_registered=bir_vat_registered,
            compliance_notes=compliance_notes,
            feature_flags_json=feature_flags_json,
            pos_profile=pos_profile,
        )
        flash("Tenant updated.", "success")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("superadmin.super_admin_dashboard"))


@superadmin_bp.route("/tenants/deactivate", methods=["POST"], endpoint="deactivate_tenant")
@super_admin_required
@require_recent_reauth()
def deactivate_tenant() -> str:
    tenant_id = request.form.get("tenant_id")
    if not tenant_id:
        flash("Tenant not found.", "error")
        return redirect(url_for("superadmin.super_admin_dashboard"))

    admin_service.set_tenant_active(int(tenant_id), False)
    flash("Tenant deactivated.", "success")
    return redirect(url_for("superadmin.super_admin_dashboard"))


@superadmin_bp.route("/tenants/reactivate", methods=["POST"], endpoint="reactivate_tenant")
@super_admin_required
@require_recent_reauth()
def reactivate_tenant() -> str:
    tenant_id = request.form.get("tenant_id")
    if not tenant_id:
        flash("Tenant not found.", "error")
        return redirect(url_for("superadmin.super_admin_dashboard"))

    admin_service.set_tenant_active(int(tenant_id), True)
    flash("Tenant reactivated.", "success")
    return redirect(url_for("superadmin.super_admin_dashboard"))


@superadmin_bp.route("/tenants/delete", methods=["POST"], endpoint="delete_tenant")
@super_admin_required
@require_recent_reauth()
def delete_tenant() -> str:
    tenant_id = request.form.get("tenant_id")
    if not tenant_id:
        flash("Tenant not found.", "error")
        return redirect(url_for("superadmin.super_admin_dashboard"))

    admin_service.delete_tenant(int(tenant_id))
    flash("Tenant deleted.", "success")
    return redirect(url_for("superadmin.super_admin_dashboard"))


@superadmin_bp.route("/pos-profiles", methods=["GET"], endpoint="pos_profiles_reference")
@super_admin_required
def pos_profiles_reference() -> str:
    context = admin_service.fetch_pos_profiles_reference_page()
    return render_template("super_admin_pos_profiles.html", **context)


@superadmin_bp.route("/payment-gateways", methods=["GET"], endpoint="payment_gateways")
@super_admin_required
def payment_gateways() -> str:
    context = admin_service.fetch_payment_gateways_admin_page()
    return render_template("super_admin_payment_gateways.html", **context)


@superadmin_bp.route("/payment-gateways/save", methods=["POST"], endpoint="save_payment_gateways")
@super_admin_required
@require_recent_reauth()
def save_payment_gateways() -> str:
    enabled = set(request.form.getlist("enabled_gateway"))
    admin_service.save_payment_gateway_platform_toggles(enabled)
    flash("Payment gateway availability updated for the platform.", "success")
    return redirect(url_for("superadmin.payment_gateways"))


@superadmin_bp.route("/settings/save", methods=["POST"], endpoint="save_saas_settings")
@super_admin_required
@require_recent_reauth()
def save_saas_settings() -> str:
    support_email = request.form.get("support_email", "").strip()
    company_name = request.form.get("company_name", "").strip()
    support_phone = request.form.get("support_phone", "").strip()
    support_url = request.form.get("support_url", "").strip()
    default_timezone = request.form.get("default_timezone", "").strip() or "Asia/Manila"
    date_format = request.form.get("date_format", "").strip() or "Y-m-d"
    maintenance_message = request.form.get("maintenance_message", "").strip() or (
        "We are performing scheduled maintenance. Please try again shortly."
    )

    lang_raw = request.form.get("platform_default_language", "en").strip().lower()
    lang = localization_service.normalize_locale(lang_raw)
    allowed = set(localization_service.supported_locales)
    if lang not in allowed:
        flash(f"Language '{lang_raw}' is not installed (no translations file). Reverted to English.", "warning")
        lang = "en" if "en" in allowed else next(iter(sorted(allowed)))

    allow_public_signup = "1" if request.form.get("allow_public_signup") == "1" else "0"
    platform_maintenance_mode = "1" if request.form.get("platform_maintenance_mode") == "1" else "0"

    admin_service.save_platform_settings(
        {
            "support_email": support_email,
            "company_name": company_name,
            "support_phone": support_phone,
            "support_url": support_url,
            "platform_default_language": lang,
            "default_timezone": default_timezone,
            "date_format": date_format,
            "allow_public_signup": allow_public_signup,
            "platform_maintenance_mode": platform_maintenance_mode,
            "maintenance_message": maintenance_message,
        }
    )
    flash("Platform settings saved.", "success")
    return redirect(_superadmin_dashboard_with_tab("settings"))


@superadmin_bp.route("/users/deactivate", methods=["POST"], endpoint="deactivate_platform_user")
@super_admin_required
@require_recent_reauth()
def deactivate_platform_user() -> str:
    user_id = request.form.get("user_id")
    acting_id = session.get("user_id")
    if not user_id or not acting_id:
        flash("Invalid request.", "error")
        return redirect(_superadmin_dashboard_with_tab("platform-admins"))
    try:
        admin_service.set_super_admin_account_active(int(user_id), False, int(acting_id))
        flash("Platform admin deactivated.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(_superadmin_dashboard_with_tab("platform-admins"))


@superadmin_bp.route("/audit/export.csv", endpoint="audit_export_csv")
@super_admin_required
def audit_export_csv() -> Response:
    payload = admin_service.stream_audit_logs_csv()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@superadmin_bp.route("/compliance/export.csv", endpoint="compliance_export_csv")
@super_admin_required
def compliance_export_csv() -> Response:
    payload = admin_service.stream_compliance_snapshot_csv()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=compliance_snapshot.csv"},
    )


@superadmin_bp.route("/tenants/bir-report-touch", methods=["POST"], endpoint="touch_bir_report")
@super_admin_required
@require_recent_reauth()
def touch_bir_report() -> str:
    tenant_id = request.form.get("tenant_id")
    if not tenant_id:
        flash("Tenant not found.", "error")
        return redirect(_superadmin_dashboard_with_tab("compliance"))
    try:
        admin_service.touch_bir_report_timestamp(int(tenant_id))
        flash("BIR report timestamp recorded (placeholder).", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(_superadmin_dashboard_with_tab("compliance"))


@superadmin_bp.route("/users/reactivate", methods=["POST"], endpoint="reactivate_platform_user")
@super_admin_required
@require_recent_reauth()
def reactivate_platform_user() -> str:
    user_id = request.form.get("user_id")
    acting_id = session.get("user_id")
    if not user_id or not acting_id:
        flash("Invalid request.", "error")
        return redirect(_superadmin_dashboard_with_tab("platform-admins"))
    try:
        admin_service.set_super_admin_account_active(int(user_id), True, int(acting_id))
        flash("Platform admin reactivated.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(_superadmin_dashboard_with_tab("platform-admins"))
