from __future__ import annotations

import json

from flask import Flask, flash, redirect, render_template, request, session, url_for

from app.core.auth import login_required
from app.core.db import get_connection
from app.core.subscription.entitlements import (
    DEMO_MAX_COMPLETED_SALES_PER_MONTH,
    DEMO_MAX_PRODUCTS,
    DEMO_PLAN_NAME,
)
from app.modules.web import queries as web_queries


def register_onboarding_routes(app: Flask) -> None:
    @app.route("/onboarding")
    @login_required("owner")
    def onboarding_home() -> str:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT name, plan_name, subscription_status FROM tenants WHERE id = ?",
                (session["tenant_id"],),
            ).fetchone()
        tenant = dict(row) if row else {}
        return render_template(
            "onboarding.html",
            tenant=tenant,
            demo_plan=DEMO_PLAN_NAME,
            demo_max_products=DEMO_MAX_PRODUCTS,
            demo_max_sales_month=DEMO_MAX_COMPLETED_SALES_PER_MONTH,
        )

    @app.route("/onboarding/complete", methods=["POST"])
    @login_required("owner")
    def onboarding_complete() -> str:
        acknowledge = request.form.get("acknowledge") == "1"
        if not acknowledge:
            flash("Please confirm you have reviewed the getting-started steps.", "warning")
            return redirect(url_for("onboarding_home"))

        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("Session expired. Please sign in again.", "error")
            return redirect(url_for("login"))

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (tenant_id, key, value)
                VALUES (?, 'onboarding_completed', '1')
                ON CONFLICT (tenant_id, key) DO UPDATE SET value = '1'
                """,
                (tenant_id,),
            )
            web_queries.log_audit(
                connection,
                "onboarding_complete",
                "tenant",
                int(tenant_id),
                "Owner completed guided onboarding checklist.",
            )

        flash("Great — your dashboard is ready. Add inventory, then open POS when you are ready to sell.", "success")
        return redirect(url_for("owner_dashboard"))


def _parse_plan_features(raw: object) -> dict[str, object]:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def register_subscription_pages_routes(app: Flask) -> None:
    @app.route("/subscription/plans")
    @login_required("owner", "admin")
    def subscription_plans() -> str:
        tenant_id = session.get("tenant_id")
        if not tenant_id:
            flash("No tenant in session.", "error")
            return redirect(url_for("login"))

        with get_connection() as connection:
            plan_rows = connection.execute(
                """
                SELECT id, name, price, features, is_active
                FROM plans
                WHERE is_active = 1
                ORDER BY price ASC, name ASC
                """
            ).fetchall()
            tenant_row = connection.execute(
                """
                SELECT id, name, plan_name, monthly_fee, subscription_status, billing_currency
                FROM tenants WHERE id = ?
                """,
                (tenant_id,),
            ).fetchone()

        plans_out: list[dict[str, object]] = []
        for r in plan_rows:
            d = dict(r)
            d["features_parsed"] = _parse_plan_features(d.get("features"))
            plans_out.append(d)

        return render_template(
            "subscription_plans.html",
            plans=plans_out,
            tenant=dict(tenant_row) if tenant_row else {},
        )

    @app.route("/subscription/select-plan", methods=["POST"])
    @login_required("owner")
    def subscription_select_plan() -> str:
        raw_id = request.form.get("plan_id", "").strip()
        try:
            plan_id = int(raw_id)
        except ValueError:
            flash("Invalid plan.", "error")
            return redirect(url_for("subscription_plans"))

        tenant_id = session.get("tenant_id")
        if not tenant_id:
            return redirect(url_for("login"))

        from app.core.subscription.service import SubscriptionService

        with get_connection() as connection:
            plan = connection.execute(
                "SELECT id, name, price FROM plans WHERE id = ? AND is_active = 1",
                (plan_id,),
            ).fetchone()
            if plan is None:
                flash("That plan is not available.", "error")
                return redirect(url_for("subscription_plans"))
            if str(plan["name"]).strip().lower() == DEMO_PLAN_NAME:
                flash("You are already on the free demo. Choose a paid plan to unlock full limits.", "info")
                return redirect(url_for("subscription_plans"))

            pname = str(plan["name"]).strip()
            price = float(plan["price"] or 0)
            connection.execute(
                """
                UPDATE tenants
                SET plan_name = ?, monthly_fee = ?, subscription_status = 'active'
                WHERE id = ?
                """,
                (pname, price, tenant_id),
            )
            sub = SubscriptionService(connection)
            existing = sub.get_subscription_by_tenant(int(tenant_id))
            if existing is None:
                sub.create_subscription(int(tenant_id), int(plan["id"]), trial_days=None)
            else:
                sub.upgrade_subscription(int(tenant_id), int(plan["id"]))

            web_queries.log_audit(
                connection,
                "plan_selected",
                "tenant",
                int(tenant_id),
                f"Owner selected plan '{pname}' at monthly fee {price}.",
            )

        flash(
            "Your workspace is now on the selected plan with full platform limits. "
            "Configure how you collect subscription fees (bank transfer, e-wallet, PayPal, etc.) under Owner settings when you go live.",
            "success",
        )
        return redirect(url_for("owner_dashboard"))
