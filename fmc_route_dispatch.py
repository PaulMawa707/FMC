from ast import literal_eval
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
import streamlit as st


WAREHOUSES = {
    "FMC": {"lat":-1.188615, "lon": 36.9118451858266},
}

DEFAULT_WORKBOOK = "route coordinates (004).xlsx"
DEFAULT_FLEET_WORKBOOK = "FCL_Vehicles.xlsx"
DEFAULT_DELIVERY_SUFFIX = "DEL"
DEFAULT_COLLECTION_SUFFIX = "COL"
DEFAULT_SERVICE_TIME_SECONDS = 30 * 60
DEFAULT_ADVANCE_TIME_SECONDS = 30 * 60
REMOTE_API_URL = "https://hst-api.wialon.com/wialon/ajax.html"
LOGISTICS_API_URL = "https://logistics.wialon.com/api/route"
LOGISTICS_ROUTES_URL = "https://logistics.wialon.com/api/routes"
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
                .stApp {
                    background:
                        radial-gradient(circle at top right, rgba(255, 214, 196, 0.18), transparent 24%),
                        radial-gradient(circle at bottom left, rgba(255, 184, 150, 0.12), transparent 22%),
                        linear-gradient(180deg, #7d1714 0%, #981f1a 42%, #b43325 100%);
                    color: #fff7f0;
                }

                .stApp [data-testid="stHeader"] {
                    background: rgba(0, 0, 0, 0);
                }

                .stApp [data-testid="stSidebar"] {
                    display: none;
                }

                .stApp [data-testid="collapsedControl"] {
                    display: none;
                }

                .stApp section[data-testid="stSidebar"] {
                    display: none;
                }

                .section-shell {
                    background: rgba(255, 255, 255, 0.76);
                    border: 1px solid rgba(111, 23, 21, 0.12);
                    border-left: 5px solid #981f1a;
                    border-radius: 16px;
                    padding: 0.9rem 1rem;
                    margin: 1rem 0 0.8rem;
                    box-shadow: 0 10px 22px rgba(65, 42, 30, 0.05);
                }

                .section-title {
                    margin: 0;
                    color: #6f1715;
                    font-size: 1.08rem;
                    font-weight: 700;
                }

                .section-caption {
                    margin: 0.2rem 0 0;
                    color: #5f4b43;
                    font-size: 0.92rem;
                }

                .stButton > button {
                    background: linear-gradient(135deg, #981f1a, #bf3a2b);
                    color: white;
                    border: none;
                    border-radius: 10px;
                }

                .stButton > button:hover {
                    background: linear-gradient(135deg, #821814, #a62f23);
                    color: white;
                }

                .stTextInput label p,
                .stDateInput label p,
                .stTimeInput label p,
                .stSelectbox label p,
                .stCheckbox label p,
                .stFileUploader label p {
                    color: #4d2f29 !important;
                    font-weight: 600;
                }

                .stDateInput input,
                .stNumberInput input {
                    background-color: rgba(255, 255, 255, 0.96) !important;
                    color: #2f241f !important;
                    -webkit-text-fill-color: #2f241f !important;
                    border: 1px solid rgba(111, 23, 21, 0.28) !important;
                    border-radius: 10px !important;
                }

                .stTextInput input,
                .stTextInput textarea {
                    background: linear-gradient(135deg, #7d1714, #9c251d) !important;
                    color: #fff7f0 !important;
                    -webkit-text-fill-color: #fff7f0 !important;
                    border: 1px solid rgba(255, 247, 240, 0.32) !important;
                    border-radius: 10px !important;
                    caret-color: #fff7f0 !important;
                }

                .stTimeInput input {
                    background: linear-gradient(135deg, #7d1714, #9c251d) !important;
                    color: #fff7f0 !important;
                    -webkit-text-fill-color: #fff7f0 !important;
                    border: 1px solid rgba(255, 247, 240, 0.32) !important;
                    border-radius: 10px !important;
                }

                .stTimeInput input[type="time"] {
                    font-weight: 600 !important;
                    letter-spacing: 0.02em;
                    caret-color: #fff7f0 !important;
                }

                .stTimeInput input[type="time"]::-webkit-datetime-edit,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-fields-wrapper,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-hour-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-minute-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-ampm-field,
                .stTimeInput input[type="time"]::-webkit-datetime-edit-text {
                    color: #fff7f0 !important;
                    -webkit-text-fill-color: #fff7f0 !important;
                }

                .stTimeInput input[type="time"]::-webkit-calendar-picker-indicator {
                    filter: brightness(0) invert(1);
                    opacity: 1;
                }

                .stTextInput input::placeholder,
                .stTextInput textarea::placeholder {
                    color: rgba(255, 247, 240, 0.72) !important;
                }

                .stDateInput input::placeholder,
                .stTimeInput input::placeholder {
                    color: #8c776f !important;
                }

                .stSelectbox div[data-baseweb="select"] > div {
                    background-color: rgba(255, 255, 255, 0.96) !important;
                    border: 1px solid rgba(111, 23, 21, 0.28) !important;
                    border-radius: 10px !important;
                    color: #2f241f !important;
                    -webkit-text-fill-color: #2f241f !important;
                }

                .stSelectbox div[data-baseweb="select"] *,
                .stDateInput * {
                    color: #2f241f !important;
                    -webkit-text-fill-color: #2f241f !important;
                }

                .stSelectbox div[data-baseweb="select"] * {
                    background-color: transparent !important;
                }

                div[role="listbox"] {
                    background-color: rgba(255, 250, 246, 0.98) !important;
                    color: #2f241f !important;
                }

                div[role="option"] {
                    background-color: rgba(255, 250, 246, 0.98) !important;
                    color: #2f241f !important;
                }

                .stTextInput input:disabled,
                .stTextArea textarea:disabled {
                    background-color: rgba(244, 236, 228, 0.98) !important;
                    color: #4d2f29 !important;
                    -webkit-text-fill-color: #4d2f29 !important;
                    opacity: 1 !important;
                }

                .stCaption {
                    color: #6a5047 !important;
                }

                .stCheckbox div[data-testid="stMarkdownContainer"] p {
                    color: #4d2f29 !important;
                }

                div[data-testid="stDataFrame"] {
                    border: 1px solid rgba(111, 23, 21, 0.12);
                    border-radius: 14px;
                    overflow: hidden;
                    box-shadow: 0 8px 18px rgba(65, 42, 30, 0.05);
                }

                .brand-link {
                    color: #981f1a !important;
                    font-weight: 600;
                    text-decoration: none;
                }

                .login-card {
                    background: rgba(255, 248, 243, 0.94);
                    border: 1px solid rgba(255, 255, 255, 0.24);
                    border-radius: 24px;
                    padding: 1.4rem 1.25rem;
                    box-shadow: 0 18px 42px rgba(77, 22, 18, 0.2);
                    backdrop-filter: blur(10px);
                    margin-top: 2.4rem;
                }

                .login-kicker {
                    color: #981f1a;
                    font-size: 0.82rem;
                    font-weight: 700;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    margin-bottom: 0.35rem;
                }

                .login-title {
                    color: #5a1512;
                    font-size: 1.8rem;
                    font-weight: 700;
                    line-height: 1.12;
                    margin-bottom: 0.35rem;
                }

                .login-copy {
                    color: #704f46;
                    font-size: 0.98rem;
                    margin-bottom: 1rem;
                }

                .login-help {
                    color: #7f665e;
                    font-size: 0.9rem;
                    margin-top: 0.9rem;
                }

                .login-image-frame {
                    background: rgba(255, 255, 255, 0.12);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 24px;
                    padding: 0.65rem;
                    box-shadow: 0 18px 42px rgba(77, 22, 18, 0.2);
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
    return df[["CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT", "PRIORITY"]]


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
    return df[["CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT", "PRIORITY"]]


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
    fleet_df["asset_label"] = fleet_df["asset_name"] + " (ID: " + fleet_df["itemid"].astype(str) + ")"
    return fleet_df[["asset_name", "asset_norm", "itemid", "asset_label"]]


def expand_route_orders(
    orders_df: pd.DataFrame,
    delivery_suffix: str,
    collection_suffix: str,
    reverse_collection_order: bool,
) -> pd.DataFrame:
    base_orders = orders_df.copy().reset_index(drop=True)

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
    return expanded


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
):
    try:
        session_id, _ = login_wialon_session(token)
        warehouse = WAREHOUSES[warehouse_choice]
        wh_lat, wh_lon = warehouse["lat"], warehouse["lon"]
        current_time = int(time.time())
        route_id = int(time.time())
        sequence_index = 0
        planned_visit_time = int(tf)
        route_orders = []

        route_orders.append(
            {
                "uid": int(unit_id),
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
                "f": 260,
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

        prev = {"y": wh_lat, "x": wh_lon}
        for idx, row in orders_df.reset_index(drop=True).iterrows():
            lat = float(row["LAT"])
            lon = float(row["LONG"])
            order_name = str(row.get("DISPLAY NAME") or row.get("CUSTOMER NAME") or f"Stop {idx + 1}")
            location = str(row.get("LOCATION") or row.get("CUSTOMER NAME") or "Unknown")
            weight_kg = int(float(row.get("TONNAGE", 0) or 0) * 1000)
            cost_val = float(row.get("AMOUNT", 0) or 0)
            priority = int(row.get("PRIORITY", idx + 1))
            service_time_seconds = int(row.get("SERVICE TIME SECONDS", DEFAULT_SERVICE_TIME_SECONDS) or DEFAULT_SERVICE_TIME_SECONDS)
            advance_time_seconds = int(row.get("ADVANCE TIME SECONDS", DEFAULT_ADVANCE_TIME_SECONDS) or DEFAULT_ADVANCE_TIME_SECONDS)
            polyline, mileage, travel_time_seconds = get_osrm_polyline(prev, {"y": lat, "x": lon})
            planned_visit_time += travel_time_seconds
            sequence_index += 1

            route_orders.append(
                {
                    "uid": int(unit_id),
                    "id": idx + 1,
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
                    },
                    "f": 0,
                    "tf": tf,
                    "tt": tt,
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
            )
            planned_visit_time += service_time_seconds
            prev = {"y": lat, "x": lon}

        polyline_back, mileage_back, travel_time_back = get_osrm_polyline(prev, {"y": wh_lat, "x": wh_lon})
        planned_visit_time += travel_time_back
        sequence_index += 1
        route_orders.append(
            {
                "uid": int(unit_id),
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
                "f": 264,
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

        batch_payload = {
            "svc": "core/batch",
            "params": json.dumps(
                {
                    "params": [
                        {
                            "svc": "order/route_update",
                            "params": {
                                "itemId": int(resource_id),
                                "orders": route_orders,
                                "routeId": route_id,
                                "callMode": "create",
                                "exp": 0,
                                "f": 0,
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
                    "flags": 0,
                }
            ),
            "sid": session_id,
        }

        route_result = requests.post(REMOTE_API_URL, data=batch_payload, timeout=90).json()
        if isinstance(route_result, list):
            first = route_result[0]
            if isinstance(first, dict) and first.get("error", 0) == 0:
                planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
                return {"error": 0, "message": "Route created successfully", "planning_url": planning_url}
            return {"error": first.get("error", 1), "message": format_wialon_error(first)}
        if isinstance(route_result, dict) and route_result.get("error", 0) == 0:
            planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
            return {"error": 0, "message": "Route created successfully", "planning_url": planning_url}
        if isinstance(route_result, dict) and route_result.get("orders"):
            planning_url = f"https://apps.wialon.com/logistics/?lang=en&sid={session_id}#/distrib/step3"
            return {"error": 0, "message": "Route created successfully", "planning_url": planning_url}
        return {"error": 1, "message": format_wialon_error(route_result)}
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
    render_user_bar()
    st.caption(
        "Loads route sheets such as eastlands, ngong rd, and southlands, then creates a Logistics route "
        "with delivery and collection suffixes."
    )

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

    render_section_header("Route Setup", "Choose route labels, warehouse, and the exact dispatch time window.")
    config_col1, config_col2, config_col3, config_col4 = st.columns(4)
    with config_col1:
        delivery_suffix = st.text_input("Delivery suffix", value=DEFAULT_DELIVERY_SUFFIX).strip() or DEFAULT_DELIVERY_SUFFIX
        collection_suffix = (
            st.text_input("Collection suffix", value=DEFAULT_COLLECTION_SUFFIX).strip() or DEFAULT_COLLECTION_SUFFIX
        )
    with config_col2:
        warehouse_choice = st.selectbox("Warehouse", list(WAREHOUSES.keys()))
        reverse_collection_order = st.checkbox("Reverse collection order", value=True)
    with config_col3:
        start_date = st.date_input("Start date")
        start_clock = st.time_input("Start time", value=dt_time(hour=6, minute=0), step=60)
    with config_col4:
        end_date = st.date_input("End date", value=start_date)
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
    )

    preview_col1, preview_col2 = st.columns(2)
    with preview_col1:
        render_section_header("Base Stops", "Original outlet list exactly as read from the selected workbook sheet.")
        st.dataframe(
            selected_route["orders"][["PRIORITY", "CUSTOMER NAME", "LOCATION", "LAT", "LONG", "TONNAGE", "AMOUNT"]],
            use_container_width=True,
        )
    with preview_col2:
        render_section_header("Logistics Stop Sequence", "Expanded stop order with delivery and collection points as they will be sent.")
        preview_df = expanded_orders.copy()
        preview_df["SEQUENCE"] = range(1, len(preview_df) + 1)
        st.dataframe(
            preview_df[
                [
                    "SEQUENCE",
                    "STOP TYPE",
                    "DISPLAY NAME",
                    "LOCATION",
                    "LAT",
                    "LONG",
                    "SERVICE TIME SECONDS",
                    "ADVANCE TIME SECONDS",
                ]
            ],
            use_container_width=True,
        )

    render_section_header("Logistics Dispatch", "Authenticate and send the selected route directly to Logistics.")
    dispatch_col1, dispatch_col2 = st.columns(2)
    with dispatch_col1:
        token = st.text_input("Logistics token", type="password")
        resource_id = st.text_input("Logistics resource ID")
    with dispatch_col2:
        if not fleet_df.empty:
            asset_label = st.selectbox("Vehicle", fleet_df["asset_label"].tolist())
            selected_asset = fleet_df[fleet_df["asset_label"] == asset_label].iloc[0]
            vehicle_name = str(selected_asset["asset_name"])
            unit_id = str(selected_asset["itemid"])
            st.caption(f"Fleet source: `{get_source_name(fleet_source)}`")
            st.text_input("Vehicle name", value=vehicle_name, disabled=True)
            st.text_input("Logistics unit ID", value=unit_id, disabled=True)
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
        if not token or not resource_id or not vehicle_name or not unit_id:
            st.error("Token, resource ID, vehicle name, and unit ID are required.")
            return

        tz = pytz.timezone("Africa/Nairobi")
        start_time = tz.localize(datetime.combine(start_date, start_clock))
        end_time = tz.localize(datetime.combine(end_date, end_clock))
        tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
        if tt <= tf:
            st.error("End date and time must be after the start date and time.")
            return

        with st.spinner("Dispatching route to Logistics..."):
            result = send_orders_and_create_route(
                token=token,
                resource_id=int(resource_id),
                unit_id=int(unit_id),
                vehicle_name=vehicle_name,
                route_name=selected_route["route_name"],
                orders_df=expanded_orders,
                tf=tf,
                tt=tt,
                warehouse_choice=warehouse_choice,
            )

        if result.get("error") == 0:
            st.success("Route dispatched successfully.")
        else:
            st.error(f"Dispatch failed: {result.get('message', 'Unknown error')}")


if __name__ == "__main__":
    run_fmc_dispatch()
