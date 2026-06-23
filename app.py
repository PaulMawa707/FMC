import os
from datetime import datetime
from functools import wraps
from hmac import compare_digest
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pytz
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import fmc_route_dispatch as dispatch

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
CONTROLTECH_URL = "https://www.controltech-ea.com/"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")


def static_asset_exists(relative_path: str) -> bool:
    return (APP_DIR / "static" / relative_path).exists()


def controltech_logo_path() -> str | None:
    for name in ("img/controltech.png", "img/controltech_logo.png"):
        if static_asset_exists(name):
            return name
    return None


def df_to_records(df):
    return df.replace({np.nan: None}).to_dict(orient="records")


def template_context(**extra):
    ct_logo = controltech_logo_path()
    return {
        "logo_exists": static_asset_exists("img/farmers_choice_logo.png"),
        "hero_exists": static_asset_exists("img/farmers_choice_login_hero.jpg"),
        "ct_logo_exists": ct_logo is not None,
        "ct_logo_path": ct_logo or "img/controltech.png",
        "controltech_url": CONTROLTECH_URL,
        **extra,
    }


def get_auth_users():
    return dispatch.get_auth_users()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def parse_datetime_local(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    tz = pytz.timezone("Africa/Nairobi")
    if parsed.tzinfo is None:
        return tz.localize(parsed)
    return parsed.astimezone(tz)


def normalize_route_param(route_name: str) -> str:
    """Decode URL-encoded route names (Vercel may pass %20 literally in path segments)."""
    return unquote(route_name or "").strip()


@app.route("/")
def index():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = get_auth_users()

        if not users:
            error = "Login credentials are not configured. Set LOGISTICS_APP_USERNAME and LOGISTICS_APP_PASSWORD in .env."
        else:
            expected = users.get(username)
            if expected and compare_digest(password, expected):
                session["authenticated"] = True
                session["username"] = username
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."

    return render_template("login.html", error=error, **template_context())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    active_tab = request.args.get("tab", "dispatch")
    if active_tab not in {"dispatch", "fleet"}:
        active_tab = "dispatch"

    return render_template(
        "dashboard.html",
        title="Farmer's Choice · Route Dispatch",
        active_tab=active_tab,
        username=session.get("username", ""),
        warehouses=list(dispatch.WAREHOUSES.keys()),
        **template_context(),
    )


@app.route("/api/routes")
@login_required
def api_routes():
    workbook_source = dispatch.resolve_workbook_source(None)
    if workbook_source is None:
        return jsonify({"ok": False, "error": f"Default workbook not found: {dispatch.DEFAULT_WORKBOOK}"}), 404

    try:
        routes, parse_errors = dispatch.load_routes_from_workbook(workbook_source)
        table_df = dispatch.build_routes_table(
            routes,
            dispatch.DEFAULT_DELIVERY_SUFFIX,
            dispatch.DEFAULT_COLLECTION_SUFFIX,
            reverse_collection_order=True,
        )
        return jsonify(
            {
                "ok": True,
                "workbook": dispatch.get_source_name(workbook_source),
                "routes": df_to_records(table_df),
                "route_names": [route["route_name"] for route in routes],
                "parse_errors": parse_errors,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/routes/<path:route_name>/preview")
@login_required
def api_route_preview(route_name):
    route_name = normalize_route_param(route_name)
    workbook_source = dispatch.resolve_workbook_source(None)
    if workbook_source is None:
        return jsonify({"ok": False, "error": f"Default workbook not found: {dispatch.DEFAULT_WORKBOOK}"}), 404

    try:
        routes, _ = dispatch.load_routes_from_workbook(workbook_source)
        selected = next((route for route in routes if route["route_name"] == route_name), None)
        if selected is None:
            return jsonify({"ok": False, "error": f"Route not found: {route_name}"}), 404

        expanded = dispatch.expand_route_orders(
            selected["orders"],
            delivery_suffix=dispatch.DEFAULT_DELIVERY_SUFFIX,
            collection_suffix=dispatch.DEFAULT_COLLECTION_SUFFIX,
            reverse_collection_order=True,
            collection_offset_meters=float(dispatch.DEFAULT_COLLECTION_OFFSET_METERS),
        )
        ready, summary = dispatch.validate_expanded_orders_for_dispatch(expanded)

        base_columns = ["PRIORITY", "CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT"]
        preview_columns = [
            "SEQUENCE",
            "STOP TYPE",
            "CUSTOMER KEY",
            "DISPLAY NAME",
            "LOCATION",
            "LAT",
            "LONG",
            "COL OFFSET BEARING",
            "SERVICE TIME SECONDS",
            "ADVANCE TIME SECONDS",
        ]
        preview_df = expanded.copy()
        preview_df["SEQUENCE"] = range(1, len(preview_df) + 1)

        default_vehicle = dispatch.DEFAULT_ROUTE_VEHICLE_MAP.get(route_name, "")

        return jsonify(
            {
                "ok": True,
                "route_name": route_name,
                "sheet_name": selected["sheet_name"],
                "parser": selected["parser"],
                "default_vehicle": default_vehicle,
                "dispatch_ready": ready,
                "dispatch_summary": summary,
                "base_stops": df_to_records(selected["orders"][base_columns]),
                "expanded_stops": df_to_records(
                    preview_df[[col for col in preview_columns if col in preview_df.columns]]
                ),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/fleet")
@login_required
def api_fleet():
    fleet_source = dispatch.resolve_fleet_source()
    if fleet_source is None:
        return jsonify(
            {
                "ok": True,
                "fleet": [],
                "message": f"No fleet file found. Expected `{dispatch.DEFAULT_FLEET_WORKBOOK}` next to this app.",
            }
        )

    try:
        fleet_df = dispatch.read_fleet_assets(fleet_source)
        return jsonify(
            {
                "ok": True,
                "workbook": dispatch.get_source_name(fleet_source),
                "fleet": df_to_records(fleet_df),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/dispatch", methods=["POST"])
@login_required
def api_dispatch():
    if not dispatch.LOGISTICS_TOKEN or not dispatch.LOGISTICS_RESOURCE_ID:
        return jsonify(
            {
                "ok": False,
                "error": "Wialon credentials are not configured. Set LOGISTICS_TOKEN and LOGISTICS_RESOURCE_ID in .env.",
            }
        ), 500

    payload = request.get_json(silent=True) or {}
    route_name = normalize_route_param(str(payload.get("route_name", "")))
    vehicle_name = str(payload.get("vehicle_name", "")).strip()
    unit_id = str(payload.get("unit_id", "")).strip()
    route_from = str(payload.get("route_from", "")).strip()
    route_end = str(payload.get("route_end", "")).strip()
    warehouse = str(payload.get("warehouse", "")).strip() or next(iter(dispatch.WAREHOUSES))

    if not route_name or not vehicle_name or not unit_id:
        return jsonify({"ok": False, "error": "Route, vehicle name, and unit ID are required."}), 400
    if warehouse not in dispatch.WAREHOUSES:
        return jsonify({"ok": False, "error": f"Unknown warehouse: {warehouse}"}), 400

    try:
        start_time = parse_datetime_local(route_from)
        end_time = parse_datetime_local(route_end)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid route start or end datetime."}), 400

    tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
    if tt <= tf:
        return jsonify({"ok": False, "error": "Route end must be after route start."}), 400

    workbook_source = dispatch.resolve_workbook_source(None)
    if workbook_source is None:
        return jsonify({"ok": False, "error": f"Default workbook not found: {dispatch.DEFAULT_WORKBOOK}"}), 404

    try:
        routes, _ = dispatch.load_routes_from_workbook(workbook_source)
        selected = next((route for route in routes if route["route_name"] == route_name), None)
        if selected is None:
            return jsonify({"ok": False, "error": f"Route not found: {route_name}"}), 404

        expanded = dispatch.expand_route_orders(
            selected["orders"],
            delivery_suffix=dispatch.DEFAULT_DELIVERY_SUFFIX,
            collection_suffix=dispatch.DEFAULT_COLLECTION_SUFFIX,
            reverse_collection_order=True,
            collection_offset_meters=float(dispatch.DEFAULT_COLLECTION_OFFSET_METERS),
        )
        ready, summary = dispatch.validate_expanded_orders_for_dispatch(expanded)
        if not ready:
            return jsonify({"ok": False, "error": f"Cannot dispatch: {summary}"}), 400

        result = dispatch.send_orders_and_create_route(
            token=dispatch.LOGISTICS_TOKEN,
            resource_id=dispatch.LOGISTICS_RESOURCE_ID,
            unit_id=int(unit_id),
            vehicle_name=vehicle_name,
            route_name=selected["route_name"],
            orders_df=expanded,
            tf=tf,
            tt=tt,
            warehouse_choice=warehouse,
            strict_visit_sequence=False,
        )

        if result.get("error") == 0:
            return jsonify(
                {
                    "ok": True,
                    "message": result.get("message", "Route dispatched successfully."),
                    "planning_url": result.get("planning_url"),
                }
            )

        return jsonify(
            {
                "ok": False,
                "error": result.get("message", "Dispatch failed."),
                "planning_url": result.get("planning_url"),
            }
        ), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
