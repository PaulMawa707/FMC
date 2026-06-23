from ast import literal_eval
import hashlib
import io
import json
import math
import os
import re
import time
from datetime import datetime, time as dt_time
from hmac import compare_digest
from pathlib import Path
from textwrap import dedent

import pandas as pd
import pytz
import requests
from dotenv import load_dotenv

try:
    import streamlit as st
except ImportError:
    st = None

load_dotenv()


WAREHOUSES = {
    "FMC": {"lat": -1.188615, "lon": 36.911845},
}

DEFAULT_WORKBOOK = "route coordinates (004).xlsx"
DEFAULT_FLEET_WORKBOOK = "FCL_Vehicles.xlsx"
DEFAULT_DELIVERY_SUFFIX = "DEL"
DEFAULT_COLLECTION_SUFFIX = "COL"
DEFAULT_SERVICE_TIME_SECONDS = 15 * 60
DEFAULT_ADVANCE_TIME_SECONDS = 0
DEFAULT_COLLECTION_OFFSET_METERS = 25
DEFAULT_ROUTE_VEHICLE_MAP = {
    "eastlands route": "FCL - KBT 227L",
    "ngong rd route": "FCL - KBV 586L",
    "southlands route": "FCL - KCF 844G",
}
# Wialon order flags (bitmask). See Wialon Remote API `order/update` docs.
# - 0x1: complete if there is at least one message in the order area with zero speed
# - 0x2: complete after leaving the order area
# - 0x4: start warehouse
# - 0x8: final warehouse
ORDER_FLAG_COMPLETE_ON_STOP = 0x1
ORDER_FLAG_COMPLETE_ON_LEAVE = 0x2
ORDER_FLAG_START_WAREHOUSE = 0x4
ORDER_FLAG_END_WAREHOUSE = 0x8
ROUTE_FLAG_ANY_SEQUENCE = 0
ROUTE_FLAG_STRICT_SEQUENCE = 1
REMOTE_API_URL = "https://hst-api.wialon.com/wialon/ajax.html"
LOGISTICS_API_URL = "https://logistics.wialon.com/api/route"
LOGISTICS_ROUTES_URL = "https://logistics.wialon.com/api/routes"
LOGISTICS_TOKEN = os.getenv("LOGISTICS_TOKEN", "").strip()
LOGISTICS_RESOURCE_ID = int(os.getenv("LOGISTICS_RESOURCE_ID", "0") or 0)
FARMERS_CHOICE_WEBSITE_URL = "https://farmerschoice.co.ke/"
FARMERS_CHOICE_LOGO_URL = "https://farmerschoice.co.ke/wp-content/uploads/2025/05/farmers-choice-logo.png"
LOCAL_BRAND_LOGO_FILE = "farmers_choice_logo.png"
LOCAL_LOGIN_IMAGE_FILE = "farmers_choice_login_hero.jpg"
AUTH_SESSION_KEY = "logistics_app_authenticated"
AUTH_USER_KEY = "logistics_app_username"


os.environ["TZ"] = "Africa/Nairobi"
try:
    time.tzset()
except Exception:
    pass


def normalize_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\u00A0", " ")).strip()


def normalize_header(value) -> str:
    return normalize_text(value).upper()


def normalize_route_name(name: str) -> str:
    clean_name = normalize_text(name)
    if not clean_name:
        return "Route"
    if clean_name.lower().endswith("route"):
        return clean_name
    return f"{clean_name} route"


def normalize_plate(value) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def get_brand_logo_path():
    local_logo_path = Path(__file__).with_name(LOCAL_BRAND_LOGO_FILE)
    if local_logo_path.exists():
        return str(local_logo_path)
    return ""


def get_login_image_path():
    for candidate in (LOCAL_LOGIN_IMAGE_FILE, "download.jfif"):
        login_image_path = Path(__file__).with_name(candidate)
        if login_image_path.exists():
            return str(login_image_path)
    return ""


def get_secrets_file_candidates():
    return [
        Path.home() / ".streamlit" / "secrets.toml",
        Path(__file__).parent / ".streamlit" / "secrets.toml",
        Path.cwd() / ".streamlit" / "secrets.toml",
    ]


def read_auth_config_from_secrets_file():
    auth_values = {}
    for secrets_path in get_secrets_file_candidates():
        if not secrets_path.exists():
            continue

        current_section = ""
        try:
            for raw_line in secrets_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1].strip()
                    continue
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
                    try:
                        key = str(literal_eval(key))
                    except Exception:
                        key = key.strip('"').strip("'")
                value = value.strip()
                try:
                    parsed_value = literal_eval(value)
                except Exception:
                    parsed_value = value.strip('"').strip("'")

                if current_section == "auth":
                    auth_values[key] = str(parsed_value)
                elif current_section == "auth.users":
                    auth_values.setdefault("users", {})[key] = str(parsed_value)
                elif current_section == "" and key in ("LOGISTICS_APP_USERNAME", "LOGISTICS_APP_PASSWORD"):
                    auth_values[key] = str(parsed_value)
        except Exception:
            continue

        if auth_values:
            break

    return auth_values


def get_auth_users():
    file_auth = read_auth_config_from_secrets_file()
    username = os.getenv("LOGISTICS_APP_USERNAME", "").strip()
    password = os.getenv("LOGISTICS_APP_PASSWORD", "")

    users = {}
    file_users = file_auth.get("users", {})
    if isinstance(file_users, dict):
        users.update({str(key).strip(): str(value) for key, value in file_users.items() if str(key).strip()})

    username = username or file_auth.get("LOGISTICS_APP_USERNAME", "").strip() or file_auth.get("username", "").strip()
    password = password or file_auth.get("LOGISTICS_APP_PASSWORD", "") or file_auth.get("password", "")

    if username and password:
        users.setdefault(username, password)

    return users


def logout_user():
    st.session_state[AUTH_SESSION_KEY] = False
    st.session_state.pop(AUTH_USER_KEY, None)


def render_login_page():
    render_branding()

    configured_users = get_auth_users()
    image_col, form_col = st.columns([1.15, 1], gap="large")

    with image_col:
        login_image_path = get_login_image_path()
        if login_image_path:
            st.markdown('<div class="login-image-frame">', unsafe_allow_html=True)
            st.image(login_image_path, use_column_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.info(f"Login image not found: `{LOCAL_LOGIN_IMAGE_FILE}`")

    with form_col:
        st.markdown(
            dedent(
                """
                <div class="login-card">
                    <div class="login-kicker">Secure Access</div>
                    <div class="login-title">Sign in to access the Logistics dispatch console</div>
                    <div class="login-copy">Use your staff credentials to open route planning, fleet assignment, and dispatch tools.</div>
                """
            ),
            unsafe_allow_html=True,
        )

        if not configured_users:
            st.error("Login credentials are not configured yet.")
            st.markdown("Set them in Streamlit Cloud secrets or local environment variables:")
            st.code(
                dedent(
                    """
                    [auth.users]
                    paul = "your-password"
                    staff_1 = "another-password"
                    staff_2 = "another-password"
                    """
                ),
                language="toml",
            )
            st.markdown(
                "Or use environment variables for a single login: "
                "`LOGISTICS_APP_USERNAME` and `LOGISTICS_APP_PASSWORD`."
            )
            st.markdown("</div>", unsafe_allow_html=True)
            return

        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            login_clicked = st.form_submit_button("Login")

        st.markdown(
            '<div class="login-help">Only authorized staff can access dispatch and route creation tools.</div></div>',
            unsafe_allow_html=True,
        )

        if login_clicked:
            entered_username = username.strip()
            configured_password = configured_users.get(entered_username)
            username_ok = entered_username in configured_users
            password_ok = bool(configured_password) and compare_digest(password, configured_password)
            if username_ok and password_ok:
                st.session_state[AUTH_SESSION_KEY] = True
                st.session_state[AUTH_USER_KEY] = entered_username
                st.rerun()
            else:
                st.error("Invalid username or password.")


def require_login() -> bool:
    if st.session_state.get(AUTH_SESSION_KEY):
        return True
    render_login_page()
    return False


def render_user_bar():
    username = st.session_state.get(AUTH_USER_KEY, "")
    left_col, right_col = st.columns([5, 1])
    with left_col:
        if username:
            st.caption(f"Signed in as `{username}`")
    with right_col:
        if st.button("Logout", key="logout_button"):
            logout_user()
            st.rerun()


def render_branding():
    st.markdown(
        dedent(
            """
            <style>
                :root {
                    --fc-bg: #eef3f7;
                    --fc-surface: #ffffff;
                    --fc-accent: #1b5e4b;
                    --fc-accent-hover: #164a3c;
                    --fc-accent-soft: #e8f3ef;
                    --fc-text: #1a2b33;
                    --fc-muted: #5a6d78;
                    --fc-border: rgba(27, 94, 75, 0.16);
                }

                .stApp {
                    background:
                        radial-gradient(circle at 8% 12%, rgba(27, 94, 75, 0.07), transparent 34%),
                        radial-gradient(circle at 92% 88%, rgba(59, 130, 180, 0.06), transparent 30%),
                        linear-gradient(165deg, #f8fbfd 0%, var(--fc-bg) 55%, #e8eef3 100%);
                    color: var(--fc-text);
                }

                .stApp [data-testid="stHeader"] {
                    background: rgba(0, 0, 0, 0);
                }

                .stApp [data-testid="stSidebar"],
                .stApp [data-testid="collapsedControl"],
                .stApp section[data-testid="stSidebar"] {
                    display: none;
                }

                h1, h2, h3, .stMarkdown p, .stMarkdown li {
                    color: var(--fc-text);
                }

                .section-shell {
                    background: var(--fc-surface);
                    border: 1px solid var(--fc-border);
                    border-left: 4px solid var(--fc-accent);
                    border-radius: 14px;
                    padding: 0.85rem 1rem;
                    margin: 0.75rem 0 0.65rem;
                    box-shadow: 0 6px 20px rgba(26, 43, 51, 0.06);
                }

                .section-title {
                    margin: 0;
                    color: var(--fc-accent);
                    font-size: 1.05rem;
                    font-weight: 700;
                }

                .section-caption {
                    margin: 0.2rem 0 0;
                    color: var(--fc-muted);
                    font-size: 0.9rem;
                }

                .stButton > button {
                    background: linear-gradient(135deg, var(--fc-accent) 0%, #227a63 100%);
                    color: #fff;
                    border: none;
                    border-radius: 10px;
                    font-weight: 600;
                    box-shadow: 0 3px 10px rgba(27, 94, 75, 0.22);
                }

                .stButton > button:hover {
                    background: var(--fc-accent-hover);
                    color: #fff;
                    border: none;
                }

                .stTextInput label p,
                .stDateInput label p,
                .stTimeInput label p,
                .stSelectbox label p,
                .stCheckbox label p,
                .stFileUploader label p,
                .stNumberInput label p {
                    color: var(--fc-text) !important;
                    font-weight: 600;
                }

                .stTextInput input,
                .stTextInput textarea,
                .stDateInput input,
                .stTimeInput input,
                .stNumberInput input {
                    background-color: var(--fc-surface) !important;
                    color: var(--fc-text) !important;
                    -webkit-text-fill-color: var(--fc-text) !important;
                    border: 1px solid var(--fc-border) !important;
                    border-radius: 10px !important;
                    caret-color: var(--fc-text) !important;
                }

                .stTimeInput input[type="time"] {
                    font-weight: 600 !important;
                }

                .stTimeInput input[type="time"]::-webkit-datetime-edit,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-fields-wrapper,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-hour-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-minute-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-ampm-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-text {
                    color: var(--fc-text) !important;
                    -webkit-text-fill-color: var(--fc-text) !important;
                }

                .stSelectbox div[data-baseweb="select"] > div {
                    background-color: var(--fc-surface) !important;
                    border: 1px solid var(--fc-border) !important;
                    border-radius: 10px !important;
                    color: var(--fc-text) !important;
                }

                .stSelectbox div[data-baseweb="select"] *,
                .stDateInput * {
                    color: var(--fc-text) !important;
                }

                div[role="listbox"],
                div[role="option"] {
                    background-color: var(--fc-surface) !important;
                    color: var(--fc-text) !important;
                }

                .stTextInput input:disabled,
                .stTextArea textarea:disabled {
                    background-color: #e4eaef !important;
                    color: var(--fc-muted) !important;
                    opacity: 1 !important;
                }

                .stCaption {
                    color: var(--fc-muted) !important;
                }

                .stCheckbox div[data-testid="stMarkdownContainer"] p {
                    color: var(--fc-text) !important;
                }

                div[data-testid="stDataFrame"] {
                    border: 1px solid var(--fc-border);
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 14px rgba(26, 43, 51, 0.05);
                }

                .brand-link {
                    color: var(--fc-accent) !important;
                    font-weight: 600;
                    text-decoration: none;
                }

                .login-card {
                    background: var(--fc-surface);
                    border: 1px solid var(--fc-border);
                    border-radius: 20px;
                    padding: 1.4rem 1.25rem;
                    box-shadow: 0 14px 36px rgba(26, 43, 51, 0.09);
                    margin-top: 2rem;
                }

                .login-kicker {
                    color: var(--fc-accent);
                    font-size: 0.82rem;
                    font-weight: 700;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    margin-bottom: 0.35rem;
                }

                .login-title {
                    color: var(--fc-text);
                    font-size: 1.75rem;
                    font-weight: 700;
                    line-height: 1.12;
                    margin-bottom: 0.35rem;
                }

                .login-copy,
                .login-help {
                    color: var(--fc-muted);
                    font-size: 0.95rem;
                }

                .login-help {
                    margin-top: 0.9rem;
                }

                .login-image-frame {
                    background: var(--fc-surface);
                    border: 1px solid var(--fc-border);
                    border-radius: 20px;
                    padding: 0.65rem;
                    box-shadow: 0 12px 32px rgba(44, 36, 32, 0.08);
                    margin-top: 1.2rem;
                }
            </style>
            """
        ),
        unsafe_allow_html=True,
    )
    logo_path = get_brand_logo_path()
    logo_col1, title_col, logo_col2 = st.columns([1.2, 5, 1.2])
    with logo_col1:
        if logo_path:
            st.image(logo_path, width=140)
    with title_col:
        st.markdown("## Farmer's Choice Route Dispatch")
        st.caption("FMC dispatch planning for Logistics with delivery and collection routing.")
        st.markdown(
            f'Brand styling inspired by the public Farmers Choice website at '
            f'<a class="brand-link" href="{FARMERS_CHOICE_WEBSITE_URL}" target="_blank">farmerschoice.co.ke</a>.',
            unsafe_allow_html=True,
        )
    with logo_col2:
        if logo_path:
            st.image(logo_path, width=110)
    st.divider()
def render_section_header(title: str, caption: str):
    st.markdown(
        dedent(
            f"""
            <div class="section-shell">
                <p class="section-title">{title}</p>
                <p class="section-caption">{caption}</p>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def format_wialon_error(payload, fallback="Unknown error"):
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and detail:
            return json.dumps(detail)
        if payload.get("reason"):
            return str(payload["reason"])
        if payload.get("message"):
            return str(payload["message"])
        if "error" in payload:
            return f"Logistics error {payload['error']}: {json.dumps(payload)}"
        return json.dumps(payload)
    if isinstance(payload, list):
        return json.dumps(payload)
    return str(payload or fallback)


def extract_coordinates(coord_str):
    if not isinstance(coord_str, str):
        return None, None
    try:
        text = coord_str.strip().upper().replace(" ", "")
        if "LAT:" in text and "LONG:" in text:
            parts = text.split("LONG:")
            lat = float(parts[0].replace("LAT:", ""))
            lon = float(parts[1])
            return lat, lon
        if "," in text:
            nums = re.findall(r"-?\d+\.\d+", text)
            if len(nums) >= 2:
                return float(nums[0]), float(nums[1])
    except Exception:
        pass
    return None, None


def read_source_bytes(source) -> bytes:
    if hasattr(source, "getvalue"):
        return source.getvalue()
    if isinstance(source, (str, os.PathLike, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise ValueError("Unsupported workbook source.")


def get_source_name(source) -> str:
    if hasattr(source, "name"):
        return Path(source.name).name
    return Path(source).name


def find_header_row(raw_df: pd.DataFrame, required_headers: set[str]):
    for idx, row in raw_df.iterrows():
        values = {normalize_header(cell) for cell in row.tolist() if normalize_text(cell)}
        if required_headers.issubset(values):
            return idx
    return None


def clean_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    cleaned_cols = []
    keep_mask = []
    for col in df.columns:
        col_str = str(col)
        col_norm = normalize_header(col_str)
        is_unnamed = col_norm.startswith("UNNAMED")
        has_lat_long_header = bool(re.search(r"\bLAT\s*:\s*.*\bLONG\s*:", col_norm))
        is_numeric_header = False
        try:
            float(col_str)
            is_numeric_header = True
        except Exception:
            is_numeric_header = False
        keep = not (is_unnamed or has_lat_long_header or is_numeric_header or not col_norm)
        cleaned_cols.append(col_norm)
        keep_mask.append(keep)
    df.columns = cleaned_cols
    return df.loc[:, keep_mask]


def read_delivery_sheet(excel_file: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    raw_df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
    required = {"CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "COORDINATES"}
    header_row = find_header_row(raw_df, required)
    if header_row is None:
        raise ValueError("Delivery-sheet header not found.")

    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    df = clean_dataframe_columns(df)

    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = df[df["CUSTOMER ID"].notna()].copy()
    df = df[~df["CUSTOMER NAME"].astype(str).str.contains("TOTAL", case=False, na=False)]

    for col in ("TONNAGE", "AMOUNT"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(),
                errors="coerce",
            ).fillna(0)
        else:
            df[col] = 0

    coords = df["COORDINATES"].apply(lambda value: pd.Series(extract_coordinates(value)))
    coords.columns = ["LAT", "LONG"]
    df = pd.concat([df, coords], axis=1)
    df = df.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)
    df["LAT"] = df["LAT"].astype(float)
    df["LONG"] = df["LONG"].astype(float)
    df["LOCATION"] = df["LOCATION"].fillna(df["CUSTOMER NAME"]).astype(str)
    df["PRIORITY"] = range(1, len(df) + 1)
    df["CUSTOMER ID"] = df["CUSTOMER ID"].astype(str).str.strip()
    return df[["CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT", "PRIORITY"]]


def read_coordinate_sheet(excel_file: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    raw_df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
    required = {"OUTLET", "LATITUDE", "LONGITUDE"}
    header_row = find_header_row(raw_df, required)
    if header_row is None:
        raise ValueError("Coordinate-sheet header not found.")

    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
    df = clean_dataframe_columns(df)

    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = df[df["OUTLET"].notna()].copy()
    df["CUSTOMER NAME"] = df["OUTLET"].astype(str).map(normalize_text)
    df = df[df["CUSTOMER NAME"] != ""].reset_index(drop=True)
    df["LAT"] = pd.to_numeric(df["LATITUDE"], errors="coerce")
    df["LONG"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    df = df.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)
    df["LOCATION"] = df["CUSTOMER NAME"]
    df["TONNAGE"] = 0.0
    df["AMOUNT"] = 0.0
    df["PRIORITY"] = range(1, len(df) + 1)
    df["CUSTOMER ID"] = df["CUSTOMER NAME"].astype(str).map(normalize_text)
    return df[["CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT", "PRIORITY"]]


def customer_key_from_row(row) -> str:
    customer_id = normalize_text(row.get("CUSTOMER ID", ""))
    if customer_id:
        return customer_id
    customer_key = normalize_text(row.get("CUSTOMER KEY", ""))
    if customer_key:
        return customer_key
    return normalize_text(row.get("CUSTOMER NAME", ""))


def make_route_order_uid(route_id: int, sequence_index: int) -> int:
    """Unique Wialon order UID per route stop (separate delivery vs collection)."""
    return int(route_id) * 10000 + int(sequence_index)


def allocate_route_id() -> int:
    """Unique route id even when several routes are dispatched within the same second."""
    return int(time.time_ns() // 1_000_000)


def summarize_expanded_orders(orders_df: pd.DataFrame) -> dict:
    stop_types = orders_df.get("STOP TYPE", pd.Series(dtype=str)).astype(str).str.lower()
    deliveries = int((stop_types == "delivery").sum())
    collections = int((stop_types == "collection").sum())
    display_names = orders_df.get("DISPLAY NAME", orders_df.get("CUSTOMER NAME", pd.Series(dtype=str))).astype(str)
    del_suffix = int(display_names.str.contains(r" - DEL$", case=False, regex=True, na=False).sum())
    col_suffix = int(display_names.str.contains(r" - COL$", case=False, regex=True, na=False).sum())
    return {
        "total": len(orders_df),
        "deliveries": deliveries,
        "collections": collections,
        "del_suffix": del_suffix,
        "col_suffix": col_suffix,
    }


def validate_expanded_orders_for_dispatch(orders_df: pd.DataFrame) -> tuple[bool, str]:
    summary = summarize_expanded_orders(orders_df)
    if summary["total"] == 0:
        return False, "No stops to dispatch."
    if summary["deliveries"] == 0:
        return False, "No delivery stops found in expanded route."
    if summary["collections"] == 0:
        return False, "No collection stops found. Each outlet should have a delivery and a collection stop."
    if summary["deliveries"] != summary["collections"]:
        return (
            False,
            f"Delivery/collection mismatch: {summary['deliveries']} deliveries vs "
            f"{summary['collections']} collections.",
        )
    if summary["del_suffix"] != summary["deliveries"] or summary["col_suffix"] != summary["collections"]:
        return False, "Stop names are missing - DEL or - COL suffixes."
    return (
        True,
        f"{summary['deliveries']} deliveries + {summary['collections']} collections "
        f"({summary['total']} stops).",
    )


def validate_created_route_orders(created_orders: list[dict], expected_count: int) -> tuple[bool, str]:
    if len(created_orders) < expected_count:
        return (
            False,
            f"Wialon created {len(created_orders)} orders but {expected_count} were sent. "
            "The route is incomplete; delete it in Logistics and dispatch again.",
        )

    # Wialon route_update responses often omit - DEL / - COL suffixes in order names even
    # when Logistics stores them correctly (visible in driver reports and the Logistics UI).
    return True, f"Verified {len(created_orders)} route orders created in Logistics."


def compute_stop_schedule(orders_df: pd.DataFrame, wh_lat: float, wh_lon: float, tf: int) -> list[dict]:
    schedule = []
    planned_visit_time = int(tf)
    prev = {"y": wh_lat, "x": wh_lon}
    for idx, row in orders_df.reset_index(drop=True).iterrows():
        lat = float(row["LAT"])
        lon = float(row["LONG"])
        service_time_seconds = int(
            row.get("SERVICE TIME SECONDS", DEFAULT_SERVICE_TIME_SECONDS) or DEFAULT_SERVICE_TIME_SECONDS
        )
        polyline, mileage, travel_time_seconds = get_osrm_polyline(prev, {"y": lat, "x": lon})
        planned_visit_time += travel_time_seconds
        schedule.append(
            {
                "row_index": int(idx),
                "row": row,
                "planned_visit_time": planned_visit_time,
                "travel_time_seconds": travel_time_seconds,
                "mileage": mileage,
                "polyline": polyline,
                "service_time_seconds": service_time_seconds,
                "stop_type": normalize_text(row.get("STOP TYPE", "")),
            }
        )
        planned_visit_time += service_time_seconds
        prev = {"y": lat, "x": lon}
    return schedule


def load_routes_from_workbook(source):
    workbook_bytes = read_source_bytes(source)
    source_name = get_source_name(source)
    workbook_stem = Path(source_name).stem
    excel_file = pd.ExcelFile(io.BytesIO(workbook_bytes))

    routes = []
    parse_errors = {}
    for sheet_name in excel_file.sheet_names:
        last_error = None
        for parser_name, parser in (("coordinate", read_coordinate_sheet), ("delivery", read_delivery_sheet)):
            try:
                orders_df = parser(excel_file, sheet_name)
                if orders_df.empty:
                    raise ValueError("No valid stop rows found.")
                route_name = normalize_route_name(sheet_name)
                if parser_name == "delivery" and len(excel_file.sheet_names) == 1:
                    route_name = normalize_route_name(workbook_stem)
                routes.append(
                    {
                        "sheet_name": sheet_name,
                        "route_name": route_name,
                        "source_name": source_name,
                        "parser": parser_name,
                        "orders": orders_df.reset_index(drop=True),
                    }
                )
                last_error = None
                break
            except Exception as exc:
                last_error = str(exc)
        if last_error:
            parse_errors[sheet_name] = last_error

    if not routes:
        raise ValueError(f"No valid route sheets found in {source_name}. Details: {parse_errors}")
    return routes, parse_errors


def read_fleet_assets(source) -> pd.DataFrame:
    df = pd.read_excel(source)
    df.columns = [normalize_header(col).replace(" ", "") for col in df.columns]

    name_col = None
    for candidate in ("REPORTNAME", "NAME", "UNIT", "UNITNAME"):
        if candidate in df.columns:
            name_col = candidate
            break

    if name_col is None or "ITEMID" not in df.columns:
        raise ValueError("Fleet file must include a vehicle name column and an itemId column.")

    fleet_df = df[[name_col, "ITEMID"]].dropna().copy()
    fleet_df["itemid"] = pd.to_numeric(fleet_df["ITEMID"], errors="coerce")
    fleet_df = fleet_df.dropna(subset=["itemid"]).copy()
    fleet_df["itemid"] = fleet_df["itemid"].astype(int)
    fleet_df["asset_name"] = fleet_df[name_col].astype(str).map(normalize_text)
    fleet_df = fleet_df[fleet_df["asset_name"] != ""].copy()
    fleet_df["asset_norm"] = fleet_df["asset_name"].map(normalize_plate)
    fleet_df = fleet_df.sort_values(["asset_norm", "asset_name"]).drop_duplicates(subset=["itemid"]).reset_index(drop=True)
    return fleet_df[["asset_name", "asset_norm", "itemid"]]


def expand_route_orders(
    orders_df: pd.DataFrame,
    delivery_suffix: str,
    collection_suffix: str,
    reverse_collection_order: bool,
    collection_offset_meters: float = DEFAULT_COLLECTION_OFFSET_METERS,
) -> pd.DataFrame:
    base_orders = orders_df.copy().reset_index(drop=True)
    if "CUSTOMER ID" in base_orders.columns:
        base_orders["CUSTOMER KEY"] = base_orders["CUSTOMER ID"].astype(str).map(normalize_text)
    else:
        base_orders["CUSTOMER KEY"] = base_orders["CUSTOMER NAME"].astype(str).map(normalize_text)

    deliveries = base_orders.copy()
    deliveries["STOP TYPE"] = "Delivery"
    deliveries["DISPLAY NAME"] = deliveries["CUSTOMER NAME"].astype(str).map(normalize_text) + f" - {delivery_suffix}"
    deliveries["PRIORITY"] = range(1, len(deliveries) + 1)
    deliveries["SERVICE TIME SECONDS"] = DEFAULT_SERVICE_TIME_SECONDS
    deliveries["ADVANCE TIME SECONDS"] = DEFAULT_ADVANCE_TIME_SECONDS

    collections = base_orders.iloc[::-1].reset_index(drop=True) if reverse_collection_order else base_orders.copy()
    collections["STOP TYPE"] = "Collection"
    collections["DISPLAY NAME"] = collections["CUSTOMER NAME"].astype(str).map(normalize_text) + f" - {collection_suffix}"
    collections["TONNAGE"] = 0.0
    collections["AMOUNT"] = 0.0
    collections["PRIORITY"] = range(len(deliveries) + 1, len(deliveries) + len(collections) + 1)
    collections["SERVICE TIME SECONDS"] = DEFAULT_SERVICE_TIME_SECONDS
    collections["ADVANCE TIME SECONDS"] = DEFAULT_ADVANCE_TIME_SECONDS

    expanded = pd.concat([deliveries, collections], ignore_index=True)
    expanded["LAT"] = pd.to_numeric(expanded["LAT"], errors="coerce")
    expanded["LONG"] = pd.to_numeric(expanded["LONG"], errors="coerce")
    expanded = expanded.dropna(subset=["LAT", "LONG"]).reset_index(drop=True)

    offset_meters = float(collection_offset_meters or 0)
    if offset_meters > 0:
        collection_mask = expanded["STOP TYPE"].astype(str).str.lower() == "collection"
        for idx in expanded.index[collection_mask]:
            row = expanded.loc[idx]
            lat = float(row["LAT"])
            lon = float(row["LONG"])
            seed = customer_key_from_row(row) or str(row.get("CUSTOMER NAME", ""))
            bearing = collection_offset_bearing_degrees(seed)
            new_lat, new_lon = offset_coordinates_by_distance_and_bearing(lat, lon, offset_meters, bearing)
            expanded.at[idx, "LAT"] = new_lat
            expanded.at[idx, "LONG"] = new_lon
            expanded.at[idx, "COL OFFSET BEARING"] = bearing

    return expanded


def collection_offset_bearing_degrees(seed: str) -> float:
    """Stable pseudo-random bearing (0-360) per customer so COL points scatter around DEL."""
    digest = hashlib.sha256(normalize_text(seed).encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 36000) / 100.0


def offset_coordinates_meters(lat: float, lon: float, meters_north: float = 0.0, meters_east: float = 0.0) -> tuple[float, float]:
    """Shift a point by meters (north = +lat, east = +lon) using a local flat-earth approximation."""
    lat_offset = meters_north / 111_320.0
    lon_scale = 111_320.0 * math.cos(math.radians(lat))
    lon_offset = meters_east / lon_scale if lon_scale else 0.0
    return lat + lat_offset, lon + lon_offset


def offset_coordinates_by_distance_and_bearing(
    lat: float, lon: float, distance_meters: float, bearing_degrees: float
) -> tuple[float, float]:
    """bearing 0 = north, 90 = east."""
    bearing_rad = math.radians(bearing_degrees)
    meters_north = distance_meters * math.cos(bearing_rad)
    meters_east = distance_meters * math.sin(bearing_rad)
    return offset_coordinates_meters(lat, lon, meters_north=meters_north, meters_east=meters_east)


def calc_distance_km(y1, x1, y2, x2):
    radius_km = 6371
    y1, x1, y2, x2 = map(math.radians, [y1, x1, y2, x2])
    dlat, dlon = y2 - y1, x2 - x1
    a = math.sin(dlat / 2) ** 2 + math.cos(y1) * math.cos(y2) * math.sin(dlon / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_osrm_polyline(start, end):
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{start['x']},{start['y']};{end['x']},{end['y']}?overview=full&geometries=polyline"
        )
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("routes"):
            route = payload["routes"][0]
            return (
                route.get("geometry"),
                int(route.get("distance", 0)),
                int(route.get("duration", 0)),
            )
    except Exception:
        pass
    distance = int(calc_distance_km(start["y"], start["x"], end["y"], end["x"]) * 1000)
    fallback_speed_mps = 30 * 1000 / 3600
    duration = int(distance / fallback_speed_mps) if distance > 0 else 0
    return None, distance, duration


def login_wialon_session(token):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {"svc": "token/login", "params": json.dumps({"token": str(token).strip()})}
    result = requests.post(REMOTE_API_URL, data=payload, headers=headers, timeout=30).json()
    if "eid" not in result:
        raise ValueError(format_wialon_error(result, fallback="Token login failed"))
    return result["eid"], result


def search_wialon_item(session_id, item_id, flags=1):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "svc": "core/search_item",
        "params": json.dumps({"id": int(item_id), "flags": int(flags)}),
        "sid": session_id,
    }
    return requests.post(REMOTE_API_URL, data=payload, headers=headers, timeout=30).json()


def execute_wialon_batch(session_id, call_params: list[dict], timeout: int = 90):
    if not call_params:
        return []
    payload = {
        "svc": "core/batch",
        "params": json.dumps({"params": call_params, "flags": 0}),
        "sid": session_id,
    }
    return requests.post(REMOTE_API_URL, data=payload, timeout=timeout).json()


def extract_route_orders(route_result) -> list[dict]:
    orders: list[dict] = []

    def collect(node):
        if isinstance(node, dict):
            node_orders = node.get("orders")
            if isinstance(node_orders, list):
                for order in node_orders:
                    if isinstance(order, dict) and order.get("id") is not None:
                        orders.append(order)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(route_result)
    deduped = []
    seen_ids = set()
    for order in orders:
        order_id = int(order["id"])
        if order_id in seen_ids:
            continue
        seen_ids.add(order_id)
        deduped.append(order)
    return deduped


def assign_route_orders_to_unit(session_id, resource_id, unit_id, created_orders: list[dict]) -> tuple[bool, str]:
    order_ids = [int(order["id"]) for order in created_orders if order.get("id") is not None]
    if not order_ids:
        return False, "Route was created but Logistics did not return order IDs to assign the vehicle."

    chunk_size = 40
    assigned_count = 0
    for offset in range(0, len(order_ids), chunk_size):
        chunk = order_ids[offset : offset + chunk_size]
        batch_calls = [
            {
                "svc": "order/update",
                "params": {
                    "itemId": int(resource_id),
                    "id": order_id,
                    "u": int(unit_id),
                    "callMode": "assign",
                },
            }
            for order_id in chunk
        ]
        assign_result = execute_wialon_batch(session_id, batch_calls, timeout=120)
        if isinstance(assign_result, list):
            for item in assign_result:
                if isinstance(item, dict) and item.get("error", 0) != 0:
                    return False, format_wialon_error(item, fallback="Failed to assign vehicle to route orders.")
            assigned_count += len(chunk)
            continue
        if isinstance(assign_result, dict) and assign_result.get("error", 0) != 0:
            return False, format_wialon_error(assign_result, fallback="Failed to assign vehicle to route orders.")
        assigned_count += len(chunk)

    return True, f"Assigned vehicle to {assigned_count} route orders."


def test_wialon_access(token, resource_id, unit_id):
    checks = []
    ok = True

    try:
        session_id, login_result = login_wialon_session(token)
        user_name = (
            login_result.get("user", {}).get("nm")
            if isinstance(login_result.get("user"), dict)
            else login_result.get("user")
        )
        checks.append(("success", f"Token login succeeded{f' for {user_name}' if user_name else ''}."))
    except Exception as exc:
        return {"ok": False, "checks": [("error", str(exc))]}

    resource_result = search_wialon_item(session_id, resource_id, flags=1)
    resource_item = resource_result.get("item") if isinstance(resource_result, dict) else None
    if resource_item:
        checks.append(("success", f"Resource `{resource_item.get('nm', resource_id)}` is visible to this token."))
    else:
        ok = False
        checks.append(
            (
                "error",
                f"Resource `{resource_id}` is not accessible through the token: "
                f"{format_wialon_error(resource_result, fallback='Resource lookup failed')}",
            )
        )

    unit_result = search_wialon_item(session_id, unit_id, flags=1)
    unit_item = unit_result.get("item") if isinstance(unit_result, dict) else None
    if unit_item:
        checks.append(("success", f"Unit `{unit_item.get('nm', unit_id)}` is visible to this token."))
    else:
        ok = False
        checks.append(
            (
                "error",
                f"Unit `{unit_id}` is not accessible through the token: "
                f"{format_wialon_error(unit_result, fallback='Unit lookup failed')}",
            )
        )

    try:
        logistics_result = requests.get(
            LOGISTICS_ROUTES_URL,
            params={"resourceId": int(resource_id), "token": str(token).strip(), "unitIds": int(unit_id)},
            timeout=30,
        ).json()
        if isinstance(logistics_result, dict) and logistics_result.get("error"):
            ok = False
            checks.append(
                (
                    "error",
                    "Logistics routes endpoint rejected this token/resource/unit combination: "
                    + format_wialon_error(logistics_result),
                )
            )
        else:
            checks.append(("success", "Logistics route listing is accessible for this token and resource."))
    except Exception as exc:
        ok = False
        checks.append(("error", f"Logistics endpoint test failed: {exc}"))

    checks.append(
        (
            "info",
            "Route creation also requires the token to have `Modification of important data` enabled.",
        )
    )
    return {"ok": ok, "checks": checks}


def build_route_order_payload(
    *,
    order_uid: int,
    order_id: int,
    order_name: str,
    location: str,
    lat: float,
    lon: float,
    unit_id: int,
    route_id: int,
    sequence_index: int,
    planned_visit_time: int,
    advance_time_seconds: int,
    service_time_seconds: int,
    mileage: int,
    travel_time_seconds: int,
    weight_kg: int,
    cost_val: float,
    priority: int,
    order_flags: int,
    order_tf: int,
    order_tt: int,
    polyline,
    current_time: int,
    customer_key: str,
    stop_type: str,
    dependent_uids: list[int] | None = None,
):
    order_payload = {
        "uid": order_uid,
        "id": order_id,
        "n": order_name,
        "p": {
            "ut": service_time_seconds,
            "rep": True,
            "w": str(weight_kg),
            "c": str(int(cost_val)),
            "r": {
                "vt": planned_visit_time,
                "ndt": advance_time_seconds,
                "id": route_id,
                "i": sequence_index,
                "m": mileage,
                "t": travel_time_seconds,
            },
            "u": int(unit_id),
            "a": f"{location} ({lat}, {lon})",
            "weight": str(weight_kg),
            "cost": str(int(cost_val)),
            "pr": priority,
            "cid": f"{customer_key}|{stop_type}" if customer_key and stop_type else customer_key,
        },
        "f": order_flags,
        "tf": order_tf,
        "tt": order_tt,
        "r": 100,
        "y": lat,
        "x": lon,
        "rp": polyline,
        "s": 0,
        "sf": 0,
        "trt": advance_time_seconds,
        "st": current_time,
        "cnm": 0,
        "callMode": "create",
        "u": int(unit_id),
        "weight": str(weight_kg),
        "cost": str(int(cost_val)),
        "cargo": {"weight": str(weight_kg), "cost": str(int(cost_val))},
        "cmp": {"unitRequirements": {"values": []}},
        "gfn": {"geofences": {}},
        "ej": {},
        "cf": {},
    }
    if dependent_uids:
        order_payload["dp"] = [int(uid) for uid in dependent_uids]
    return order_payload


def send_orders_and_create_route(
    token,
    resource_id,
    unit_id,
    vehicle_name,
    route_name,
    orders_df,
    tf,
    tt,
    warehouse_choice,
    strict_visit_sequence=True,
):
    try:
        session_id, _ = login_wialon_session(token)
        warehouse = WAREHOUSES[warehouse_choice]
        wh_lat, wh_lon = warehouse["lat"], warehouse["lon"]
        current_time = int(time.time())
        route_id = allocate_route_id()
        sequence_index = 0
        planned_visit_time = int(tf)
        route_orders = []
        stop_schedule = compute_stop_schedule(orders_df, wh_lat, wh_lon, tf)

        sequence_index = 0
        delivery_uid_by_customer_key: dict[str, int] = {}
        route_orders.append(
            {
                "uid": make_route_order_uid(route_id, sequence_index),
                "id": 0,
                "n": warehouse_choice,
                "p": {
                    "ut": 0,
                    "rep": True,
                    "w": "0",
                    "c": "0",
                    "r": {
                        "vt": planned_visit_time,
                        "ndt": DEFAULT_ADVANCE_TIME_SECONDS,
                        "id": route_id,
                        "i": sequence_index,
                        "m": 0,
                        "t": 0,
                    },
                    "u": int(unit_id),
                    "a": f"{warehouse_choice} ({wh_lat}, {wh_lon})",
                    "weight": "0",
                    "cost": "0",
                },
                "f": ORDER_FLAG_START_WAREHOUSE,
                "tf": tf,
                "tt": tt,
                "r": 100,
                "y": wh_lat,
                "x": wh_lon,
                "s": 0,
                "sf": 0,
                "trt": DEFAULT_ADVANCE_TIME_SECONDS,
                "st": current_time,
                "cnm": 0,
                "callMode": "create",
                "u": int(unit_id),
                "weight": "0",
                "cost": "0",
                "cargo": {"weight": "0", "cost": "0"},
                "cmp": {"unitRequirements": {"values": []}},
                "gfn": {"geofences": {}},
                "ej": {},
                "cf": {},
            }
        )

        for schedule_entry in stop_schedule:
            row = schedule_entry["row"]
            idx = schedule_entry["row_index"]
            lat = float(row["LAT"])
            lon = float(row["LONG"])
            stop_type = schedule_entry["stop_type"] or "Delivery"
            customer_key = customer_key_from_row(row)
            order_name = str(row.get("DISPLAY NAME") or row.get("CUSTOMER NAME") or f"Stop {idx + 1}")
            location = str(row.get("LOCATION") or row.get("CUSTOMER NAME") or "Unknown")
            weight_kg = int(float(row.get("TONNAGE", 0) or 0) * 1000)
            cost_val = float(row.get("AMOUNT", 0) or 0)
            priority = int(row.get("PRIORITY", idx + 1))
            service_time_seconds = schedule_entry["service_time_seconds"]
            advance_time_seconds = int(row.get("ADVANCE TIME SECONDS", DEFAULT_ADVANCE_TIME_SECONDS) or DEFAULT_ADVANCE_TIME_SECONDS)
            planned_arrival = int(schedule_entry["planned_visit_time"])
            sequence_index += 1
            order_uid = make_route_order_uid(route_id, sequence_index)
            is_collection = stop_type.lower() == "collection"
            if is_collection:
                # Each collection opens only when this outlet's planned return visit starts,
                # not when the last delivery on the route finishes.
                order_tf = max(planned_arrival - advance_time_seconds, int(tf))
                order_tt = planned_arrival + service_time_seconds + advance_time_seconds
                paired_delivery_uid = delivery_uid_by_customer_key.get(customer_key)
                dependent_uids = [paired_delivery_uid] if paired_delivery_uid else None
            else:
                order_tf = int(tf)
                order_tt = planned_arrival + service_time_seconds + advance_time_seconds
                dependent_uids = None
                delivery_uid_by_customer_key[customer_key] = order_uid

            # Wialon requires `tf < tt` for every order.
            # If the planned schedule exceeds the selected route end time, clamp the window.
            order_tf = int(order_tf)
            order_tt = int(order_tt)
            if order_tf >= int(tt):
                order_tf = int(tt) - 1
            order_tt = min(order_tt, int(tt))
            if order_tt <= order_tf:
                order_tt = order_tf + 1

            route_orders.append(
                build_route_order_payload(
                    order_uid=order_uid,
                    order_id=idx + 1,
                    order_name=order_name,
                    location=location,
                    lat=lat,
                    lon=lon,
                    unit_id=int(unit_id),
                    route_id=route_id,
                    sequence_index=sequence_index,
                    planned_visit_time=schedule_entry["planned_visit_time"],
                    advance_time_seconds=advance_time_seconds,
                    service_time_seconds=service_time_seconds,
                    mileage=schedule_entry["mileage"],
                    travel_time_seconds=schedule_entry["travel_time_seconds"],
                    weight_kg=weight_kg,
                    cost_val=cost_val,
                    priority=priority,
                    order_flags=(ORDER_FLAG_COMPLETE_ON_STOP | ORDER_FLAG_COMPLETE_ON_LEAVE),
                    order_tf=order_tf,
                    order_tt=order_tt,
                    polyline=schedule_entry["polyline"],
                    current_time=current_time,
                    customer_key=customer_key,
                    stop_type=stop_type,
                    dependent_uids=dependent_uids,
                )
            )

        last_stop = stop_schedule[-1] if stop_schedule else None
        prev = (
            {"y": float(last_stop["row"]["LAT"]), "x": float(last_stop["row"]["LONG"])}
            if last_stop is not None
            else {"y": wh_lat, "x": wh_lon}
        )
        polyline_back, mileage_back, travel_time_back = get_osrm_polyline(prev, {"y": wh_lat, "x": wh_lon})
        planned_visit_time = (last_stop["planned_visit_time"] if last_stop else tf) + (
            last_stop["service_time_seconds"] if last_stop else 0
        )
        planned_visit_time += travel_time_back
        sequence_index += 1
        route_orders.append(
            {
                "uid": make_route_order_uid(route_id, sequence_index),
                "id": len(route_orders),
                "n": warehouse_choice,
                "p": {
                    "ut": 0,
                    "rep": True,
                    "w": "0",
                    "c": "0",
                    "r": {
                        "vt": planned_visit_time,
                        "ndt": DEFAULT_ADVANCE_TIME_SECONDS,
                        "id": route_id,
                        "i": sequence_index,
                        "m": mileage_back,
                        "t": travel_time_back,
                    },
                    "u": int(unit_id),
                    "a": f"{warehouse_choice} ({wh_lat}, {wh_lon})",
                    "weight": "0",
                    "cost": "0",
                },
                "f": ORDER_FLAG_END_WAREHOUSE,
                "tf": tf,
                "tt": tt,
                "r": 100,
                "y": wh_lat,
                "x": wh_lon,
                "rp": polyline_back,
                "s": 0,
                "sf": 0,
                "trt": DEFAULT_ADVANCE_TIME_SECONDS,
                "st": current_time,
                "cnm": 0,
                "callMode": "create",
                "u": int(unit_id),
                "weight": "0",
                "cost": "0",
                "cargo": {"weight": "0", "cost": "0"},
                "cmp": {"unitRequirements": {"values": []}},
                "gfn": {"geofences": {}},
                "ej": {},
                "cf": {},
            }
        )

        total_mileage = sum(order["p"]["r"]["m"] for order in route_orders)
        total_cost = sum(float(order["p"]["c"]) for order in route_orders if order["f"] == 0)
        total_weight = sum(int(order["p"]["w"]) for order in route_orders if order["f"] == 0)
        final_route_name = f"{normalize_route_name(route_name)} - {vehicle_name} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        route_result = execute_wialon_batch(
            session_id,
            [
                {
                    "svc": "order/route_update",
                    "params": {
                        "itemId": int(resource_id),
                        "orders": route_orders,
                        "routeId": route_id,
                        "callMode": "create",
                        "exp": 0,
                        "f": ROUTE_FLAG_STRICT_SEQUENCE if strict_visit_sequence else ROUTE_FLAG_ANY_SEQUENCE,
                        "n": final_route_name,
                        "summary": {
                            "countOrders": len(route_orders),
                            "mileage": total_mileage,
                            "priceMileage": 0,
                            "priceTotal": total_cost,
                            "weight": total_weight,
                            "cost": total_cost,
                        },
                    },
                }
            ],
            timeout=90,
        )

        create_ok = False
        if isinstance(route_result, list):
            first = route_result[0] if route_result else {}
            create_ok = isinstance(first, dict) and first.get("error", 0) == 0
            if not create_ok:
                return {"error": first.get("error", 1) if isinstance(first, dict) else 1, "message": format_wialon_error(first)}
        elif isinstance(route_result, dict):
            create_ok = route_result.get("error", 0) == 0
            if not create_ok:
                return {"error": route_result.get("error", 1), "message": format_wialon_error(route_result)}
        else:
            return {"error": 1, "message": format_wialon_error(route_result)}

        created_orders = extract_route_orders(route_result)
        verify_ok, verify_message = validate_created_route_orders(created_orders, len(route_orders))
        if not verify_ok:
            return {
                "error": 1,
                "message": verify_message,
                "planning_url": f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3",
            }
        assign_ok, assign_message = assign_route_orders_to_unit(
            session_id,
            resource_id,
            unit_id,
            created_orders,
        )
        planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
        if not assign_ok:
            return {
                "error": 1,
                "message": f"Route created but vehicle was not picked up in Logistics. {assign_message}",
                "planning_url": planning_url,
            }
        return {
            "error": 0,
            "message": f"Route created and vehicle assigned. {assign_message} {verify_message}",
            "planning_url": planning_url,
        }
    except Exception as exc:
        return {"error": 1, "message": str(exc)}


def resolve_workbook_source(uploaded_file):
    if uploaded_file is not None:
        return uploaded_file
    default_path = Path(__file__).with_name(DEFAULT_WORKBOOK)
    if default_path.exists():
        return default_path
    return None


def resolve_fleet_source():
    default_path = Path(__file__).with_name(DEFAULT_FLEET_WORKBOOK)
    if default_path.exists():
        return default_path
    return None


def build_routes_table(routes, delivery_suffix, collection_suffix, reverse_collection_order):
    rows = []
    for route in routes:
        expanded = expand_route_orders(
            route["orders"],
            delivery_suffix=delivery_suffix,
            collection_suffix=collection_suffix,
            reverse_collection_order=reverse_collection_order,
        )
        rows.append(
            {
                "Route Name": route["route_name"],
                "Sheet": route["sheet_name"],
                "Format": route["parser"],
                "Base Stops": len(route["orders"]),
                "Expanded Stops": len(expanded),
                "Total Amount": round(route["orders"]["AMOUNT"].sum(), 2),
                "Total Tonnage": round(route["orders"]["TONNAGE"].sum(), 2),
            }
        )
    return pd.DataFrame(rows)


def run_fmc_dispatch():
    st.set_page_config(page_title="FMC Route Dispatch", layout="wide", initial_sidebar_state="collapsed")
    if not require_login():
        return

    render_branding()

    workbook_source = resolve_workbook_source(None)
    if workbook_source is None:
        st.error(f"Default workbook not found: `{DEFAULT_WORKBOOK}`")
        return

    try:
        routes, _parse_errors = load_routes_from_workbook(workbook_source)
    except Exception as exc:
        st.error(f"Failed to load workbook: {exc}")
        return

    fleet_source = resolve_fleet_source()
    fleet_df = pd.DataFrame()
    fleet_error = None
    if fleet_source is not None:
        try:
            fleet_df = read_fleet_assets(fleet_source)
        except Exception as exc:
            fleet_error = str(exc)

    delivery_suffix = DEFAULT_DELIVERY_SUFFIX
    collection_suffix = DEFAULT_COLLECTION_SUFFIX
    warehouse_choice = next(iter(WAREHOUSES))
    reverse_collection_order = True
    collection_offset_meters = float(DEFAULT_COLLECTION_OFFSET_METERS)
    strict_visit_sequence = False

    date_col1, date_col2, time_col1, time_col2 = st.columns(4)
    with date_col1:
        start_date = st.date_input("Start date")
    with date_col2:
        end_date = st.date_input("End date", value=start_date)
    with time_col1:
        start_clock = st.time_input("Start time", value=dt_time(hour=4, minute=0), step=60)
    with time_col2:
        end_clock = st.time_input("End time", value=dt_time(hour=18, minute=0), step=60)

    render_section_header("Available Routes", "Review the route sheets loaded from the workbook before dispatch.")
    st.dataframe(
        build_routes_table(routes, delivery_suffix, collection_suffix, reverse_collection_order),
        use_container_width=True,
    )

    route_names = [route["route_name"] for route in routes]
    selected_route_name = st.selectbox("Route to dispatch", route_names)
    selected_route = next(route for route in routes if route["route_name"] == selected_route_name)
    expanded_orders = expand_route_orders(
        selected_route["orders"],
        delivery_suffix=delivery_suffix,
        collection_suffix=collection_suffix,
        reverse_collection_order=reverse_collection_order,
        collection_offset_meters=collection_offset_meters,
    )

    preview_col1, preview_col2 = st.columns(2)
    with preview_col1:
        render_section_header("Base Stops", "Original outlet list exactly as read from the selected workbook sheet.")
        st.dataframe(
            selected_route["orders"][["PRIORITY", "CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT"]],
            use_container_width=True,
        )
    with preview_col2:
        render_section_header(
            "Logistics Stop Sequence",
            "Deliveries use workbook coordinates; collections are offset from delivery coords per outlet. "
            "Each collection unlocks only after that outlet's delivery is done.",
        )
        preview_df = expanded_orders.copy()
        preview_df["SEQUENCE"] = range(1, len(preview_df) + 1)
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
        st.dataframe(preview_df[[col for col in preview_columns if col in preview_df.columns]], use_container_width=True)

    dispatch_ready, dispatch_summary = validate_expanded_orders_for_dispatch(expanded_orders)
    if dispatch_ready:
        st.success(f"Dispatch check passed: {dispatch_summary}")
    else:
        st.error(f"Dispatch check failed: {dispatch_summary}")

    render_section_header("Logistics Dispatch", "Choose a vehicle and send the selected route to Logistics.")
    if not fleet_df.empty:
        vehicle_options = fleet_df["asset_name"].tolist()
        default_vehicle = DEFAULT_ROUTE_VEHICLE_MAP.get(selected_route_name)
        default_index = vehicle_options.index(default_vehicle) if default_vehicle in vehicle_options else 0
        selected_vehicle_name = st.selectbox(
            "Vehicle",
            vehicle_options,
            index=default_index,
            key=f"vehicle_select_{selected_route_name}",
        )
        selected_asset = fleet_df[fleet_df["asset_name"] == selected_vehicle_name].iloc[0]
        vehicle_name = str(selected_asset["asset_name"])
        unit_id = str(selected_asset["itemid"])
    else:
        if fleet_error:
            st.warning(f"Fleet file could not be loaded: {fleet_error}")
        else:
            st.info(f"No fleet file found. Expected `{DEFAULT_FLEET_WORKBOOK}` next to this script.")
        vehicle_name = st.text_input("Vehicle name")
        unit_id = st.text_input("Logistics unit ID")

    st.session_state.pop("wialon_access_test", None)
    dispatch_clicked = st.button("Dispatch selected route", type="primary")

    if dispatch_clicked:
        if not vehicle_name or not unit_id:
            st.error("Vehicle name and unit ID are required.")
            return

        dispatch_ready, dispatch_summary = validate_expanded_orders_for_dispatch(expanded_orders)
        if not dispatch_ready:
            st.error(f"Cannot dispatch: {dispatch_summary}")
            return

        expected_vehicle = DEFAULT_ROUTE_VEHICLE_MAP.get(selected_route_name)
        if expected_vehicle and vehicle_name != expected_vehicle:
            st.warning(
                f"`{selected_route_name}` is usually assigned to `{expected_vehicle}`, "
                f"but `{vehicle_name}` is selected."
            )

        tz = pytz.timezone("Africa/Nairobi")
        start_time = tz.localize(datetime.combine(start_date, start_clock))
        end_time = tz.localize(datetime.combine(end_date, end_clock))
        tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
        if tt <= tf:
            st.error("End date and time must be after the start date and time.")
            return

        with st.spinner("Dispatching route to Logistics..."):
            result = send_orders_and_create_route(
                token=LOGISTICS_TOKEN,
                resource_id=LOGISTICS_RESOURCE_ID,
                unit_id=int(unit_id),
                vehicle_name=vehicle_name,
                route_name=selected_route["route_name"],
                orders_df=expanded_orders,
                tf=tf,
                tt=tt,
                warehouse_choice=warehouse_choice,
                strict_visit_sequence=strict_visit_sequence,
            )

        if result.get("error") == 0:
            st.success(result.get("message", "Route dispatched successfully."))
            if result.get("planning_url"):
                st.markdown(f"[Open route in Logistics]({result['planning_url']})")
        else:
            st.error(f"Dispatch failed: {result.get('message', 'Unknown error')}")
            if result.get("planning_url"):
                st.markdown(f"[Open Logistics to review or delete the partial route]({result['planning_url']})")


if __name__ == "__main__":
    if st is None:
        raise SystemExit("Streamlit is required to run the legacy UI. Install streamlit or use: python app.py")
    run_fmc_dispatch()
