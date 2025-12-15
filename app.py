import os
import base64
from datetime import datetime, timedelta

import requests
from flask import Flask, request, render_template

app = Flask(__name__)

# =========================
# ENV VARS (Railway)
# =========================
EVOCON_TENANT = os.getenv("EVOCON_TENANT", "")
EVOCON_SECRET = os.getenv("EVOCON_SECRET", "")

# =========================
# CONFIG
# =========================
ORDERED_ITEMS = [
    "Θερμοκρασία λαμινατορίου (°C)",
    "Είδος μαργαρίνης",
    "Θερμοκρασία μαργαρίνης (°C)",
    "Λαμάκι μαργαρίνης (mm)",
    "Λαμάκι recupero (mm)",
    "Διάκενο μαχαιριών (cm)",
    "Πάχος extruder (1η)",
    "Πάχος extruder (2η)",
    "Ποσοστό μαργαρίνης (%)",
    "Ποσοστό ανακύκλωσης ζύμης recupero (%)",
]

ALLOWED_ITEMS = set(ORDERED_ITEMS)

SHIFT_START = {
    "A": "06:00",
    "B": "14:00",
    "Γ": "22:00",
}

# =========================
# BASIC ROUTES
# =========================
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def home():
    return "<a href='/print'>Go to Print</a> | <a href='/health'>Health</a>"


# =========================
# HELPERS
# =========================
def basic_auth_header():
    if not EVOCON_TENANT or not EVOCON_SECRET:
        raise RuntimeError("Missing EVOCON_TENANT / EVOCON_SECRET")
    token = base64.b64encode(
        f"{EVOCON_TENANT}:{EVOCON_SECRET}".encode("utf-8")
    ).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def normalize_value(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("-", "N/A", "n/a"):
        return ""
    return s.replace(",", ".")


def parse_hhmm(s):
    try:
        return datetime.strptime(s.strip(), "%H:%M").time()
    except Exception:
        return None


def minutes(t):
    return t.hour * 60 + t.minute


def sort_donetime_list(times, shift_name):
    start_str = SHIFT_START.get(shift_name, "00:00")
    start_t = parse_hhmm(start_str) or datetime.strptime("00:00", "%H:%M").time()
    start_m = minutes(start_t)

    def key(tstr):
        t = parse_hhmm(tstr) or datetime.strptime("00:00", "%H:%M").time()
        m = minutes(t)
        return (m - start_m) % (24 * 60)

    return sorted(times, key=key)


# =========================
# EVOCON API (DATE-ONLY!)
# =========================
def fetch_checklists_json(start_date: str, end_date: str):
    """
    start_date / end_date MUST be YYYY-MM-DD
    """
    url = "https://api.evocon.com/api/reports/checklists_json"
    headers = {
        "Accept": "application/json",
        **basic_auth_header(),
    }
    params = {
        "startTime": start_date,
        "endTime": end_date,
    }

    r = requests.get(url, headers=headers, params=params, timeout=45)

    if r.status_code != 200:
        raise RuntimeError(
            f"Evocon API ERROR\n"
            f"URL: {url}\n"
            f"PARAMS: {params}\n"
            f"STATUS: {r.status_code}\n"
            f"BODY:\n{r.text[:1500]}"
        )

    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(
            f"Evocon returned NON-JSON\n"
            f"STATUS: {r.status_code}\n"
            f"ERROR: {e}\n"
            f"BODY:\n{r.text[:1500]}"
        )

    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected API response type: {type(data)}"
        )

    return data


# =========================
# DATA PROCESSING
# =========================
def build_shift_index(rows):
    """
    Find available (shiftDate, shift, station)
    """
    idx = {}
    for r in rows:
        sd = str(r.get("shiftDate") or "").strip()
        sh = str(r.get("shift") or "").strip()
        st = str(r.get("station") or "").strip()
        dt = str(r.get("donetime") or "").strip()

        if not (sd and sh and st and dt):
            continue

        t = parse_hhmm(dt)
        key = (sd, sh, st)

        if key not in idx:
            idx[key] = {
                "shiftDate": sd,
                "shift": sh,
                "station": st,
                "last_time": t,
            }
        else:
            if t and (idx[key]["last_time"] is None or t > idx[key]["last_time"]):
                idx[key]["last_time"] = t

    def sort_key(x):
        try:
            d = datetime.strptime(x["shiftDate"], "%Y-%m-%d").date()
        except Exception:
            d = datetime.min.date()
        t = x["last_time"] or datetime.min.time()
        return (d, t)

    return sorted(idx.values(), key=sort_key, reverse=True)


def build_report(rows, shiftDate, shiftName, station):
    filtered = [
        r for r in rows
        if str(r.get("shiftDate") or "").strip() == shiftDate
        and str(r.get("shift") or "").strip() == shiftName
        and str(r.get("station") or "").strip() == station
    ]

    submissions = {}
    meta = {}

    for r in filtered:
        donetime = str(r.get("donetime") or "").strip()
        itemname = str(r.get("itemname") or "").strip()

        if not donetime or itemname not in ALLOWED_ITEMS:
            continue

        submissions.setdefault(donetime, {})
        submissions[donetime][itemname] = normalize_value(r.get("itemresult"))

        if donetime not in meta:
            meta[donetime] = {
                "operator": str(r.get("operator") or "").strip(),
                "product": str(r.get("productproduced") or "").strip(),
                "productionOrder": str(r.get("productionOrder") or "").strip(),
            }

    columns = sort_donetime_list(list(submissions.keys()), shiftName)

    matrix = []
    for item in ORDERED_ITEMS:
        matrix.append({
            "label": item,
            "values": [submissions.get(t, {}).get(item, "") for t in columns]
        })

    header = {"operator": "", "product": "", "productionOrder": ""}
    if columns:
        header = meta.get(columns[-1], header)

    return {
        "columns": columns,
        "matrix": matrix,
        "header": header,
        "shiftDate": shiftDate,
        "shift": shiftName,
        "station": station,
    }


# =========================
# UI ROUTES
# =========================
@app.get("/print")
def picker():
    today = datetime.now().date()
    start_date = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    rows = fetch_checklists_json(start_date, end_date)
    shifts = build_shift_index(rows)

    if not shifts:
        return "No shifts found in last 3 days."

    return render_template("picker.html", shifts=shifts)


@app.get("/print/render")
def render_print():
    key = request.args.get("key", "")
    parts = key.split("|")
    if len(parts) != 3:
        return "Invalid selection", 400

    shiftDate, shiftName, station = parts[0].strip(), parts[1].strip(), parts[2].strip()

    try:
        day = datetime.strptime(shiftDate, "%Y-%m-%d").date()
    except Exception as e:
        return f"Bad shiftDate: {e}", 400

    start_date = (day - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = (day + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        rows = fetch_checklists_json(start_date, end_date)
        report = build_report(rows, shiftDate, shiftName, station)

        if not report["columns"]:
            return (
                f"No data found\n\n"
                f"shiftDate={shiftDate}\nshift={shiftName}\nstation={station}\n"
                f"range={start_date} → {end_date}"
            )

        return render_template("print_form.html", **report)

    except Exception as e:
        return (
            "<pre style='white-space:pre-wrap'>"
            f"ERROR:\n{e}\n\n"
            f"shiftDate={shiftDate}\nshift={shiftName}\nstation={station}\n"
            f"range={start_date} → {end_date}"
            "</pre>"
        ), 500
