#!/usr/bin/env python3
"""
Rain Monitor — India SW Monsoon Tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tracks cumulative rainfall across all 36 IMD meteorological subdivisions
during the SW monsoon window (Jun 1 – Sep 30). Reports deviation from
the 5-year LPA proxy and vs same-window last year, in IMD's standard
deficit/surplus categories.

Data: Open-Meteo daily precipitation (ERA5 + hi-res regional models).
Output: rain_monsoon_monitor.html (a self-contained dashboard).

Run:  python rain_monitor.py
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

MONSOON_START_MM_DD = (6, 1)   # SW monsoon official start
MONSOON_END_MM_DD   = (9, 30)  # SW monsoon official end
BASELINE_YEARS      = 5        # 5yr LPA proxy
API_BASE_FORECAST   = "https://api.open-meteo.com/v1/forecast"
API_BASE_ARCHIVE    = "https://archive-api.open-meteo.com/v1/archive"
API_DELAY_SEC       = 0.35     # be gentle to Open-Meteo
CACHE_DIR           = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

# ─── IMD Official Data Source ───
# imdpune.gov.in publishes the same subdivision statistics as mausam.imd.gov.in
# but as clean HTML tables. Updated weekly (typically Thursday).
IMD_URL_CUMULATIVE = "https://imdpune.gov.in/seasons/cumulative.html"
IMD_URL_WEEKBYWEEK = "https://imdpune.gov.in/seasons/weekbyweek.html"
IMD_WEEK_LABELS = [
    "Jun 3","Jun 10","Jun 17","Jun 24",
    "Jul 1","Jul 8","Jul 15","Jul 22","Jul 29",
    "Aug 5","Aug 12","Aug 19","Aug 26",
    "Sep 2","Sep 9","Sep 16","Sep 23","Sep 30",
]

# Map our subdivision codes → IMD's spelling in the imdpune tables
IMD_NAME_MAP = {
    "JK":"Jammu & Kashmir and Ladakh", "HP":"Himachal Pradesh", "PB":"Punjab",
    "HCD":"Har. Chd. & Delhi", "UK":"Uttarakhand",
    "WUP":"West Uttar Pradesh", "EUP":"East Uttar Pradesh",
    "WR":"West Rajasthan", "ER":"East Rajasthan",
    "WMP":"West Madhya Pradesh", "EMP":"East Madhya Pradesh",
    "VID":"Vidarbha", "CG":"Chhattisgarh",
    "GJ":"Gujarat Region", "SK":"Saurashtra & Kutch",
    "KG":"Konkan & Goa", "MM":"Madhya Maharashtra",
    "MW":"Marathwada", "OD":"Odisha",
    "CAP":"Coastal AP and Yanam", "TG":"Telangana", "RS":"Rayalaseema",
    "TN":"TN. Pudu.and Karaikal",
    "CK":"Coastal Karnataka", "NIK":"N. I. Karnataka", "SIK":"S. I. Karnataka",
    "KL":"Kerala & Mahe", "LD":"Lakshdweep", "AN":"A & N Islands",
    "BR":"Bihar", "JH":"Jharkhand",
    "GWB":"Gangetic West Bengal", "SWB":"SHWB & Sikkim",
    "AR":"Arunachal Pradesh", "AM":"Assam & Meghalaya", "NMMT":"N M M T",
}

# IMD deficit categories (standard)
def category(pct_dev):
    """pct_dev is percentage deviation from LPA (e.g. -22.5 means 22.5% below LPA)"""
    if pct_dev is None: return "NO DATA"
    if pct_dev >= 60:   return "LARGE EXCESS"
    if pct_dev >= 20:   return "EXCESS"
    if pct_dev >= -19:  return "NORMAL"
    if pct_dev >= -59:  return "DEFICIENT"
    return "LARGE DEFICIENT"

CATEGORY_COLOR = {
    "LARGE EXCESS":    "#1D4ED8",  # deep blue
    "EXCESS":          "#3B82F6",  # blue
    "NORMAL":          "#10B981",  # green
    "DEFICIENT":       "#F59E0B",  # amber
    "LARGE DEFICIENT": "#DC2626",  # red
    "NO DATA":         "#78716C",  # gray
}

# ═══════════════════════════════════════════════════════════════
# 36 IMD METEOROLOGICAL SUBDIVISIONS
# ═══════════════════════════════════════════════════════════════
# Each subdivision: 2-4 representative lat/lon points.
# Grouped into 4 broad homogeneous regions per IMD's national bulletin:
#   NW  = Northwest India (9)
#   C   = Central India (10)
#   S   = South Peninsula (10)
#   ENE = East and Northeast India (7)
# Total: 36

SUBDIVISIONS = [
    # ─── NORTHWEST INDIA (9) ────────────────────────────────
    {"code":"JK",  "name":"Jammu, Kashmir & Ladakh",     "region":"NW",
     "points":[("Srinagar",34.08,74.80),("Jammu",32.73,74.86),("Leh",34.15,77.58)]},
    {"code":"HP",  "name":"Himachal Pradesh",             "region":"NW",
     "points":[("Shimla",31.10,77.17),("Manali",32.24,77.19),("Dharamshala",32.22,76.32)]},
    {"code":"PB",  "name":"Punjab",                       "region":"NW",
     "points":[("Amritsar",31.63,74.87),("Ludhiana",30.90,75.85),("Jalandhar",31.33,75.58)]},
    {"code":"HCD", "name":"Haryana, Chandigarh & Delhi",  "region":"NW",
     "points":[("Delhi",28.61,77.21),("Chandigarh",30.73,76.78),("Hisar",29.15,75.72)]},
    {"code":"UK",  "name":"Uttarakhand",                  "region":"NW",
     "points":[("Dehradun",30.32,78.03),("Haridwar",29.95,78.16),("Nainital",29.38,79.46)]},
    {"code":"WUP", "name":"West Uttar Pradesh",           "region":"NW",
     "points":[("Agra",27.18,78.02),("Meerut",28.99,77.71),("Kanpur",26.45,80.33)]},
    {"code":"EUP", "name":"East Uttar Pradesh",           "region":"NW",
     "points":[("Lucknow",26.85,80.95),("Varanasi",25.32,82.97),("Gorakhpur",26.76,83.37)]},
    {"code":"WR",  "name":"West Rajasthan",               "region":"NW",
     "points":[("Jodhpur",26.24,73.02),("Bikaner",28.02,73.31),("Jaisalmer",26.92,70.90)]},
    {"code":"ER",  "name":"East Rajasthan",               "region":"NW",
     "points":[("Jaipur",26.92,75.79),("Kota",25.21,75.83),("Udaipur",24.58,73.68)]},

    # ─── CENTRAL INDIA (10) ─────────────────────────────────
    {"code":"WMP", "name":"West Madhya Pradesh",          "region":"C",
     "points":[("Indore",22.72,75.86),("Ujjain",23.18,75.78),("Bhopal",23.26,77.41)]},
    {"code":"EMP", "name":"East Madhya Pradesh",          "region":"C",
     "points":[("Jabalpur",23.18,79.99),("Rewa",24.53,81.30),("Satna",24.60,80.83)]},
    {"code":"VID", "name":"Vidarbha",                     "region":"C",
     "points":[("Nagpur",21.15,79.09),("Amravati",20.94,77.77),("Akola",20.71,77.00)]},
    {"code":"CG",  "name":"Chhattisgarh",                 "region":"C",
     "points":[("Raipur",21.25,81.63),("Bilaspur",22.09,82.15),("Durg",21.19,81.28)]},
    {"code":"GJ",  "name":"Gujarat Region",               "region":"C",
     "points":[("Ahmedabad",23.03,72.58),("Vadodara",22.31,73.18),("Surat",21.17,72.83)]},
    {"code":"SK",  "name":"Saurashtra & Kutch",           "region":"C",
     "points":[("Rajkot",22.30,70.80),("Bhuj",23.25,69.67),("Junagadh",21.52,70.46)]},
    {"code":"KG",  "name":"Konkan & Goa",                 "region":"C",
     "points":[("Mumbai",19.08,72.88),("Panaji",15.50,73.83),("Ratnagiri",16.99,73.31)]},
    {"code":"MM",  "name":"Madhya Maharashtra",           "region":"C",
     "points":[("Pune",18.52,73.86),("Nashik",19.99,73.79),("Kolhapur",16.71,74.24)]},
    {"code":"MW",  "name":"Marathwada",                   "region":"C",
     "points":[("Aurangabad",19.88,75.34),("Nanded",19.15,77.32),("Latur",18.40,76.58)]},
    {"code":"OD",  "name":"Odisha",                       "region":"C",
     "points":[("Bhubaneswar",20.30,85.82),("Cuttack",20.46,85.88),("Berhampur",19.31,84.79)]},

    # ─── SOUTH PENINSULA (10) ───────────────────────────────
    {"code":"CAP", "name":"Coastal Andhra Pradesh",       "region":"S",
     "points":[("Visakhapatnam",17.69,83.22),("Vijayawada",16.51,80.65),("Nellore",14.44,79.99)]},
    {"code":"TG",  "name":"Telangana",                    "region":"S",
     "points":[("Hyderabad",17.39,78.49),("Warangal",17.97,79.60),("Karimnagar",18.44,79.13)]},
    {"code":"RS",  "name":"Rayalaseema",                  "region":"S",
     "points":[("Tirupati",13.63,79.42),("Kurnool",15.83,78.04),("Anantapur",14.68,77.60)]},
    {"code":"TN",  "name":"Tamil Nadu, Puducherry & Karaikal", "region":"S",
     "points":[("Chennai",13.08,80.27),("Coimbatore",11.02,76.96),("Madurai",9.93,78.12),("Puducherry",11.94,79.83)]},
    {"code":"CK",  "name":"Coastal Karnataka",            "region":"S",
     "points":[("Mangalore",12.91,74.86),("Karwar",14.81,74.13),("Udupi",13.34,74.75)]},
    {"code":"NIK", "name":"North Interior Karnataka",     "region":"S",
     "points":[("Hubli",15.36,75.12),("Bijapur",16.83,75.71),("Gulbarga",17.33,76.83)]},
    {"code":"SIK", "name":"South Interior Karnataka",     "region":"S",
     "points":[("Bangalore",12.97,77.59),("Mysore",12.30,76.65),("Chitradurga",14.23,76.40)]},
    {"code":"KL",  "name":"Kerala & Mahe",                "region":"S",
     "points":[("Thiruvananthapuram",8.52,76.94),("Kochi",9.93,76.27),("Kozhikode",11.26,75.78)]},
    {"code":"LD",  "name":"Lakshadweep",                  "region":"S",
     "points":[("Kavaratti",10.57,72.64),("Minicoy",8.28,73.05)]},
    {"code":"AN",  "name":"Andaman & Nicobar Islands",    "region":"S",
     "points":[("Port Blair",11.68,92.74),("Car Nicobar",9.16,92.79)]},

    # ─── EAST & NORTHEAST INDIA (7) ─────────────────────────
    {"code":"BR",  "name":"Bihar",                        "region":"ENE",
     "points":[("Patna",25.61,85.14),("Gaya",24.79,85.00),("Muzaffarpur",26.12,85.39)]},
    {"code":"JH",  "name":"Jharkhand",                    "region":"ENE",
     "points":[("Ranchi",23.34,85.31),("Jamshedpur",22.80,86.20),("Dhanbad",23.80,86.43)]},
    {"code":"GWB", "name":"Gangetic West Bengal",         "region":"ENE",
     "points":[("Kolkata",22.57,88.36),("Asansol",23.68,86.97),("Kharagpur",22.35,87.32)]},
    {"code":"SWB", "name":"Sub-Himalayan West Bengal & Sikkim", "region":"ENE",
     "points":[("Siliguri",26.72,88.42),("Gangtok",27.33,88.61),("Darjeeling",27.04,88.26)]},
    {"code":"AR",  "name":"Arunachal Pradesh",            "region":"ENE",
     "points":[("Itanagar",27.10,93.62),("Pasighat",28.07,95.33),("Tawang",27.59,91.86)]},
    {"code":"AM",  "name":"Assam & Meghalaya",            "region":"ENE",
     "points":[("Guwahati",26.14,91.74),("Shillong",25.58,91.89),("Silchar",24.83,92.78),("Cherrapunji",25.30,91.72)]},
    {"code":"NMMT","name":"Nagaland, Manipur, Mizoram & Tripura", "region":"ENE",
     "points":[("Kohima",25.67,94.11),("Imphal",24.82,93.94),("Aizawl",23.73,92.72),("Agartala",23.83,91.28)]},
]

REGION_NAMES = {
    "NW":  "Northwest India",
    "C":   "Central India",
    "S":   "South Peninsula",
    "ENE": "East & Northeast India",
}

assert len(SUBDIVISIONS) == 36, f"Expected 36 subdivisions, got {len(SUBDIVISIONS)}"


# ═══════════════════════════════════════════════════════════════
# DATE HELPERS
# ═══════════════════════════════════════════════════════════════

def get_date_ranges():
    """Return date bounds for current monsoon, last year monsoon, and 5yr baseline."""
    today = date.today()
    yr = today.year
    # If today is before Jun 1, we're pre-monsoon — track the coming or previous?
    # Convention: from Jan-May, show LAST year's monsoon as the "current" for context.
    monsoon_start_this = date(yr, *MONSOON_START_MM_DD)
    monsoon_end_this   = date(yr, *MONSOON_END_MM_DD)

    if today < monsoon_start_this:
        # Pre-Jun: report on the last completed monsoon
        cur_year = yr - 1
    else:
        cur_year = yr

    cur_start = date(cur_year, *MONSOON_START_MM_DD)
    # cur_end: min(today, Sep 30 of cur_year) — so we don't ask for future days
    cur_end   = min(today, date(cur_year, *MONSOON_END_MM_DD))
    if cur_end < cur_start:
        cur_end = cur_start  # edge case: today == Jun 1

    prev_year = cur_year - 1
    prev_start = date(prev_year, *MONSOON_START_MM_DD)
    prev_end   = date(prev_year, cur_end.month, cur_end.day)

    # 5yr baseline: (prev_year-1) back BASELINE_YEARS years = 5 years
    baseline_years = list(range(prev_year - BASELINE_YEARS, prev_year))
    baseline_ranges = []
    for by in baseline_years:
        bs = date(by, *MONSOON_START_MM_DD)
        be = date(by, *MONSOON_END_MM_DD)
        baseline_ranges.append((by, bs, be))

    return {
        "today": today.isoformat(),
        "current_year": cur_year,
        "prev_year": prev_year,
        "current_start": cur_start.isoformat(),
        "current_end":   cur_end.isoformat(),
        "prev_start":    prev_start.isoformat(),
        "prev_end":      prev_end.isoformat(),
        "baseline_years": baseline_years,
        "baseline_ranges": [(y, s.isoformat(), e.isoformat()) for y, s, e in baseline_ranges],
        "days_elapsed": (cur_end - cur_start).days + 1,
        "total_days": (date(cur_year, *MONSOON_END_MM_DD) - cur_start).days + 1,
        "baseline_label": f"{baseline_years[0]}-{baseline_years[-1]} (5yr)",
    }


# ═══════════════════════════════════════════════════════════════
# OPEN-METEO FETCHING
# ═══════════════════════════════════════════════════════════════

def _cache_path(kind, lat, lon, start, end):
    key = f"{kind}_{lat:.3f}_{lon:.3f}_{start}_{end}.json"
    return CACHE_DIR / key

def _load_cache(p):
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _save_cache(p, data):
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def fetch_precip(lat, lon, start, end, archive=False, cache=True):
    """Fetch daily precipitation for a lat/lon between start and end (ISO dates)."""
    p = _cache_path("om_arch" if archive else "om_fcst", lat, lon, start, end)
    if cache:
        c = _load_cache(p)
        if c is not None:
            return c
    base = API_BASE_ARCHIVE if archive else API_BASE_FORECAST
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date":   end,
        "daily":      "precipitation_sum",
        "timezone":   "Asia/Kolkata",
    }
    for attempt in range(3):
        try:
            r = requests.get(base, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json().get("daily") or {}
                # Normalize shape
                out = {"time": data.get("time") or [], "precipitation_sum": data.get("precipitation_sum") or []}
                if cache:
                    _save_cache(p, out)
                time.sleep(API_DELAY_SEC)
                return out
            time.sleep(1 + attempt * 2)
        except Exception as e:
            print(f"    fetch error {lat},{lon} [{start}..{end}]: {e}", file=sys.stderr)
            time.sleep(1 + attempt * 2)
    return {"time": [], "precipitation_sum": []}


def fetch_subdivision(sub, dates):
    """Fetch precipitation for all rep points in a subdivision, for current, prev, and each baseline year."""
    result = {"code": sub["code"], "name": sub["name"], "region": sub["region"], "points": []}
    for name, lat, lon in sub["points"]:
        pt = {"name": name, "lat": lat, "lon": lon}
        # Current year — use forecast API (includes recent-day data)
        pt["current"] = fetch_precip(lat, lon, dates["current_start"], dates["current_end"], archive=False)
        # Prior year — archive
        pt["prev"] = fetch_precip(lat, lon, dates["prev_start"], dates["prev_end"], archive=True)
        # Baseline years — archive
        pt["baseline"] = {}
        for by, bs, be in dates["baseline_ranges"]:
            pt["baseline"][str(by)] = fetch_precip(lat, lon, bs, be, archive=True)
        result["points"].append(pt)
    return result


def fetch_all(dates):
    """Fetch data for every subdivision. Progress printed to stderr."""
    all_data = []
    total = len(SUBDIVISIONS)
    for i, sub in enumerate(SUBDIVISIONS, 1):
        print(f"[{i:2d}/{total}] {sub['name']} ({len(sub['points'])} pts)", file=sys.stderr)
        all_data.append(fetch_subdivision(sub, dates))
    return all_data


# ═══════════════════════════════════════════════════════════════
# IMD OFFICIAL DATA SCRAPER
# ═══════════════════════════════════════════════════════════════

import re as _re

def _parse_imd_table(html):
    """Parse IMD's cumulative.html or weekbyweek.html table.
    Returns dict: {imd_name: {week_label: dev_pct, ...}, ...}
    """
    result = {}
    trs = _re.findall(r'<tr[^>]*>(.*?)</tr>', html, _re.DOTALL | _re.IGNORECASE)
    for tr in trs[3:]:  # skip the 3 header rows
        cells = _re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, _re.DOTALL | _re.IGNORECASE)
        cells = [_re.sub(r'\s+', ' ', _re.sub(r'<[^>]+>', '', c)).strip() for c in cells]
        if len(cells) < 3:
            continue
        try:
            int(cells[0])            # Sr No must be an integer
            name = cells[1]
        except (ValueError, IndexError):
            continue
        weeks = cells[2:]
        wk_data = {}
        for wl, val in zip(IMD_WEEK_LABELS, weeks):
            if val:
                try:
                    wk_data[wl] = float(val)
                except ValueError:
                    pass
        result[name] = wk_data
    return result


def fetch_imd_official():
    """Scrape imdpune.gov.in for the official % departure from LPA.
    Returns dict keyed by subdivision code with:
      { code: {'imd_name': str, 'cumulative_weeks': {wl: dev}, 'weekly_weeks': {wl: dev},
               'latest_week': str, 'latest_cumulative_dev': float or None} }
    Returns empty dict if IMD is unreachable — script keeps working with Open-Meteo only.
    """
    out = {code: {"imd_name": name, "cumulative_weeks": {}, "weekly_weeks": {},
                  "latest_week": None, "latest_cumulative_dev": None}
           for code, name in IMD_NAME_MAP.items()}
    print("Fetching IMD official cumulative & weekly departures...", file=sys.stderr)
    try:
        cum_html = requests.get(IMD_URL_CUMULATIVE, timeout=20).text
        wkw_html = requests.get(IMD_URL_WEEKBYWEEK, timeout=20).text
    except Exception as e:
        print(f"  IMD fetch failed: {e} — dashboard will fall back to Open-Meteo only", file=sys.stderr)
        return {}
    cum_by_name = _parse_imd_table(cum_html)
    wkw_by_name = _parse_imd_table(wkw_html)
    print(f"  IMD cumulative rows: {len(cum_by_name)}", file=sys.stderr)
    print(f"  IMD weekly rows    : {len(wkw_by_name)}", file=sys.stderr)

    for code, entry in out.items():
        imd_name = entry["imd_name"]
        entry["cumulative_weeks"] = cum_by_name.get(imd_name, {})
        entry["weekly_weeks"]     = wkw_by_name.get(imd_name, {})
        # Latest populated week in cumulative table = authoritative reading
        cum = entry["cumulative_weeks"]
        # Skip 0.0 (IMD uses 0.0 as a sentinel for "missing"; real 0 is exceedingly rare)
        cum_clean = {k: v for k, v in cum.items() if abs(v) > 0.01}
        # Walk labels in order and grab the last non-empty one
        for wl in IMD_WEEK_LABELS:
            if wl in cum_clean:
                entry["latest_week"] = wl
                entry["latest_cumulative_dev"] = cum_clean[wl]
    matched = sum(1 for e in out.values() if e["latest_cumulative_dev"] is not None)
    print(f"  Matched {matched}/{len(out)} subdivisions with IMD data", file=sys.stderr)
    return out
    return all_data


# ═══════════════════════════════════════════════════════════════
# STATISTICS
# ═══════════════════════════════════════════════════════════════

def _sum(arr):
    return sum(x for x in (arr or []) if x is not None)

def _cum(arr):
    """Running cumulative sum (None treated as 0)."""
    out, r = [], 0.0
    for x in (arr or []):
        if x is not None:
            r += x
        out.append(round(r, 1))
    return out

def _len_valid(arr):
    return sum(1 for x in (arr or []) if x is not None)

def _pct_dev(actual, baseline):
    if baseline is None or baseline <= 0.001: return None
    return round(100.0 * (actual - baseline) / baseline, 1)


def compute_subdivision_stats(sub_data, dates, imd_data=None):
    """Compute rainfall statistics for a single subdivision by averaging its rep points."""
    points = sub_data["points"]
    n_pts = len(points)

    # Sum precip per point for current, prev, and each baseline year
    per_point_current  = [_sum(p["current"]["precipitation_sum"])  for p in points]
    per_point_prev     = [_sum(p["prev"]["precipitation_sum"])     for p in points]
    per_point_baseline = []  # list of dicts {year: total_mm}
    for p in points:
        per_point_baseline.append({y: _sum(p["baseline"][y]["precipitation_sum"]) for y in p["baseline"]})

    # Subdivision-level averages across points (simple mean)
    cur_total  = sum(per_point_current) / n_pts if n_pts else 0.0
    prev_total = sum(per_point_prev)    / n_pts if n_pts else 0.0

    # Baseline: for each year, average across points; then mean across years = LPA
    yearly_avgs = []
    baseline_years = sorted(per_point_baseline[0].keys()) if per_point_baseline else []
    for y in baseline_years:
        vals = [pb[y] for pb in per_point_baseline]
        yearly_avgs.append(sum(vals) / len(vals) if vals else 0.0)
    lpa_todate = (sum(yearly_avgs) / len(yearly_avgs)) if yearly_avgs else 0.0

    # Also compute FULL-season LPA (Jun 1 – Sep 30 full window) for context
    # We fetched full monsoon in baseline_ranges, so per-point baseline sum is over full monsoon.
    # But per-point current/prev is only through cur_end. So:
    # - lpa_todate ≠ full-season LPA; we need to slice baseline to same window as current.
    # Easier approach: reconstruct daily arrays and slice.
    cur_end_iso = dates["current_end"]
    days_elapsed = dates["days_elapsed"]

    def sliced_baseline_total(days):
        """Average of baseline-year totals over first `days` days of monsoon."""
        totals = []
        for p in points:
            for y, day_data in p["baseline"].items():
                arr = day_data["precipitation_sum"][:days]
                totals.append(_sum(arr))
        # Now group by (year) instead
        # Actually we want per-year avg across points, then mean across years
        year_group = {}
        for p in points:
            for y in p["baseline"]:
                arr = p["baseline"][y]["precipitation_sum"][:days]
                year_group.setdefault(y, []).append(_sum(arr))
        year_means = [sum(v)/len(v) for v in year_group.values() if v]
        return (sum(year_means)/len(year_means)) if year_means else 0.0

    # Full-season LPA (uses full monsoon baseline data)
    def full_season_baseline_total():
        year_group = {}
        for p in points:
            for y in p["baseline"]:
                year_group.setdefault(y, []).append(_sum(p["baseline"][y]["precipitation_sum"]))
        year_means = [sum(v)/len(v) for v in year_group.values() if v]
        return (sum(year_means)/len(year_means)) if year_means else 0.0

    lpa_todate = sliced_baseline_total(days_elapsed)
    lpa_full   = full_season_baseline_total()

    # Prev-year same window (already fetched over prev_start..prev_end which mirrors current window)
    # prev_total is already sliced correctly.

    dev_vs_lpa    = _pct_dev(cur_total, lpa_todate)
    dev_vs_prev   = _pct_dev(cur_total, prev_total)
    cat           = category(dev_vs_lpa)

    # Daily cumulative arrays for chart
    # Average across points day-by-day
    def avg_daily_precip(field):
        # field is 'current', 'prev', or a year string; extract per-point arrays and average
        arrays = []
        for p in points:
            if field == "current":  arr = p["current"]["precipitation_sum"]
            elif field == "prev":   arr = p["prev"]["precipitation_sum"]
            else:                    arr = p["baseline"][field]["precipitation_sum"]
            arrays.append(arr)
        max_len = max((len(a) for a in arrays), default=0)
        out = []
        for i in range(max_len):
            vals = [a[i] for a in arrays if i < len(a) and a[i] is not None]
            out.append(round(sum(vals)/len(vals), 2) if vals else None)
        return out

    cur_daily = avg_daily_precip("current")
    prev_daily = avg_daily_precip("prev")
    # LPA daily = mean across baseline years (day-by-day)
    if baseline_years:
        by_daily = [avg_daily_precip(y) for y in baseline_years]
        max_len = max((len(a) for a in by_daily), default=0)
        lpa_daily = []
        for i in range(max_len):
            vals = [a[i] for a in by_daily if i < len(a) and a[i] is not None]
            lpa_daily.append(round(sum(vals)/len(vals), 2) if vals else None)
    else:
        lpa_daily = []

    # ── Pull IMD official reading for this subdivision ──
    imd_entry = (imd_data or {}).get(sub_data["code"], {}) if imd_data else {}
    imd_dev = imd_entry.get("latest_cumulative_dev")
    imd_week = imd_entry.get("latest_week")
    imd_weekly = imd_entry.get("cumulative_weeks", {})  # trend of cumulative dev by week
    imd_cat = category(imd_dev) if imd_dev is not None else "NO DATA"

    # Primary reading — prefer IMD if available, fall back to Open-Meteo derived
    primary_dev = imd_dev if imd_dev is not None else dev_vs_lpa
    primary_cat = imd_cat if imd_dev is not None else cat
    primary_source = "IMD" if imd_dev is not None else "OM"

    return {
        "code":       sub_data["code"],
        "name":       sub_data["name"],
        "region":     sub_data["region"],
        "cur_total":  round(cur_total, 1),
        "prev_total": round(prev_total, 1),
        "lpa_todate": round(lpa_todate, 1),
        "lpa_full":   round(lpa_full, 1),
        "dev_vs_lpa":  dev_vs_lpa,
        "dev_vs_prev": dev_vs_prev,
        "category":   cat,
        "n_points":   n_pts,
        # IMD official (primary signal)
        "imd_dev":    imd_dev,
        "imd_week":   imd_week,
        "imd_weekly": imd_weekly,
        "imd_cat":    imd_cat,
        "imd_name":   imd_entry.get("imd_name"),
        # Primary reading (IMD if available, else OM)
        "primary_dev":    primary_dev,
        "primary_cat":    primary_cat,
        "primary_source": primary_source,
        # Daily arrays (for chart) — still from Open-Meteo, for daily granularity
        "cur_daily":  cur_daily,
        "prev_daily": prev_daily,
        "lpa_daily":  lpa_daily,
        "cur_cum":    _cum(cur_daily),
        "prev_cum":   _cum(prev_daily),
        "lpa_cum":    _cum(lpa_daily),
    }


def compute_regional_and_pan(stats):
    """Aggregate to 4 broad regions and pan-India."""
    def _agg(subset):
        if not subset: return None
        # Simple mean of subdivision totals (equal-weight, not area-weighted)
        cur_total  = sum(s["cur_total"]  for s in subset) / len(subset)
        prev_total = sum(s["prev_total"] for s in subset) / len(subset)
        lpa_todate = sum(s["lpa_todate"] for s in subset) / len(subset)
        lpa_full   = sum(s["lpa_full"]   for s in subset) / len(subset)
        dev_vs_lpa  = _pct_dev(cur_total, lpa_todate)
        dev_vs_prev = _pct_dev(cur_total, prev_total)
        # IMD average — mean of IMD dev% across subdivisions that have IMD data
        imd_vals = [s["imd_dev"] for s in subset if s.get("imd_dev") is not None]
        imd_avg = round(sum(imd_vals)/len(imd_vals), 1) if imd_vals else None
        imd_n   = len(imd_vals)
        primary_vals = [s["primary_dev"] for s in subset if s.get("primary_dev") is not None]
        primary_avg = round(sum(primary_vals)/len(primary_vals), 1) if primary_vals else None
        # Category counts by IMD (fallback: our category)
        imd_cats = {"LARGE EXCESS":0,"EXCESS":0,"NORMAL":0,"DEFICIENT":0,"LARGE DEFICIENT":0,"NO DATA":0}
        om_cats  = {"LARGE EXCESS":0,"EXCESS":0,"NORMAL":0,"DEFICIENT":0,"LARGE DEFICIENT":0,"NO DATA":0}
        for s in subset:
            imd_cats[s["imd_cat"]] = imd_cats.get(s["imd_cat"], 0) + 1
            om_cats[s["category"]] = om_cats.get(s["category"], 0) + 1
        # Primary cats mirror IMD when we have IMD data, else fall back to OM
        primary_cats = imd_cats if imd_n > 0 else om_cats
        return {
            "cur_total":  round(cur_total, 1),
            "prev_total": round(prev_total, 1),
            "lpa_todate": round(lpa_todate, 1),
            "lpa_full":   round(lpa_full, 1),
            "dev_vs_lpa":  dev_vs_lpa,
            "dev_vs_prev": dev_vs_prev,
            "category":   category(dev_vs_lpa),
            # IMD (primary)
            "imd_dev":    imd_avg,
            "imd_n":      imd_n,
            "imd_cat":    category(imd_avg) if imd_avg is not None else "NO DATA",
            "imd_cats":   imd_cats,
            # Primary reading — IMD if available, else OM
            "primary_dev": primary_avg,
            "primary_cat": category(primary_avg) if primary_avg is not None else "NO DATA",
            "n_subs":     len(subset),
            "cats":       primary_cats,   # primary category distribution
            "om_cats":    om_cats,        # Open-Meteo category distribution (for cross-check)
        }

    regional = {}
    for r in ["NW","C","S","ENE"]:
        subset = [s for s in stats if s["region"] == r]
        regional[r] = _agg(subset)
    pan = _agg(stats)

    # Pan-India daily cumulative (mean across subdivisions of their cum arrays)
    def _mean_daily_cum(field):
        arrs = [s[field] for s in stats if s[field]]
        if not arrs: return []
        max_len = max(len(a) for a in arrs)
        out = []
        for i in range(max_len):
            vals = [a[i] for a in arrs if i < len(a) and a[i] is not None]
            out.append(round(sum(vals)/len(vals), 1) if vals else None)
        return out

    pan["cur_cum"]  = _mean_daily_cum("cur_cum")
    pan["prev_cum"] = _mean_daily_cum("prev_cum")
    pan["lpa_cum"]  = _mean_daily_cum("lpa_cum")

    return regional, pan


# ═══════════════════════════════════════════════════════════════
# HTML GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_html(stats, regional, pan, dates):
    # Verdict emoji & message — driven by PRIMARY (IMD if available, else Open-Meteo)
    primary_dev = pan["primary_dev"] if pan and pan.get("primary_dev") is not None else (pan["dev_vs_lpa"] if pan and pan["dev_vs_lpa"] is not None else 0)
    primary_cat = pan["primary_cat"] if pan and pan.get("primary_cat") not in (None,"NO DATA") else (pan["category"] if pan else "NO DATA")
    if   primary_cat == "LARGE EXCESS":    ve, vt, vc = "💧💧", "Large Excess Rainfall — Flood Risk, Bullish for Kharif/Rural", "#1D4ED8"
    elif primary_cat == "EXCESS":          ve, vt, vc = "💧",   "Excess Rainfall — Kharif Positive, Watch Reservoirs",           "#3B82F6"
    elif primary_cat == "NORMAL":          ve, vt, vc = "☔",   "Normal Monsoon — In-Line with LPA",                              "#10B981"
    elif primary_cat == "DEFICIENT":       ve, vt, vc = "☀️",   "Deficient Rainfall — Kharif Risk, Watch Reservoir Fill",         "#F59E0B"
    elif primary_cat == "LARGE DEFICIENT": ve, vt, vc = "🌵",   "Large Deficient — Drought Watch, Cautious on Rural Consumption", "#DC2626"
    else:                                   ve, vt, vc = "❓",   "Insufficient Data",                                              "#78716C"

    # Determine primary source and latest IMD week label
    primary_source = "IMD" if pan and pan.get("imd_dev") is not None else "OM"
    imd_latest_week = None
    if primary_source == "IMD":
        # Find the most recent week label populated across all subdivisions
        for wl in reversed(IMD_WEEK_LABELS):
            if any(wl in s.get("imd_weekly", {}) for s in stats):
                imd_latest_week = wl
                break

    # Sort subdivisions worst-first by primary reading, grouped by region
    sorted_stats = sorted(
        stats,
        key=lambda s: (s["region"],
                       s["primary_dev"] if s.get("primary_dev") is not None else -999)
    )

    # Build data payload
    payload = {
        "today":          dates["today"],
        "currentYear":    dates["current_year"],
        "prevYear":       dates["prev_year"],
        "currentStart":   dates["current_start"],
        "currentEnd":     dates["current_end"],
        "daysElapsed":    dates["days_elapsed"],
        "totalDays":      dates["total_days"],
        "baselineLabel":  dates["baseline_label"],
        "generatedAt":    datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "verdictEmoji":   ve,
        "verdictText":    vt,
        "verdictColor":   vc,
        "primarySource":  primary_source,
        "imdLatestWeek":  imd_latest_week,
        "pan": pan,
        "regional": regional,
        "regionNames": REGION_NAMES,
        "subdivisions": [{
            "code":        s["code"],
            "name":        s["name"],
            "region":      s["region"],
            "curTotal":    s["cur_total"],
            "prevTotal":   s["prev_total"],
            "lpaTodate":   s["lpa_todate"],
            "lpaFull":     s["lpa_full"],
            "devVsLpa":    s["dev_vs_lpa"],       # Open-Meteo derived
            "devVsPrev":   s["dev_vs_prev"],
            "category":    s["category"],          # Open-Meteo derived
            "imdDev":      s.get("imd_dev"),       # IMD official
            "imdWeek":     s.get("imd_week"),
            "imdCat":      s.get("imd_cat"),
            "imdName":     s.get("imd_name"),
            "primaryDev":  s.get("primary_dev"),
            "primaryCat":  s.get("primary_cat"),
            "primarySrc":  s.get("primary_source"),
            "nPoints":     s["n_points"],
            "curCum":      s["cur_cum"],
            "prevCum":     s["prev_cum"],
            "lpaCum":      s["lpa_cum"],
        } for s in sorted_stats],
        "categoryColors": CATEGORY_COLOR,
    }

    tp = Path(__file__).parent / "rain_monitor_template.html"
    html = tp.read_text(encoding="utf-8") if tp.exists() else get_embedded_template()
    return html.replace("/*__DATA_BLOCK__*/", "const DATA = " + json.dumps(payload) + ";")


def get_embedded_template():
    return r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rain Monitor — India SW Monsoon</title>

<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-JHYNM09XSR"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-JHYNM09XSR');
</script>

<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700;800&family=DM+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0A0E1A;--bg2:#111827;--bg3:#1E293B;--bg4:#334155;
  --bd:#334155;--bd2:#1E293B;
  --t1:#F1F5F9;--t2:#94A3B8;--t3:#64748B;
  --bl3:#93C5FD;--bl4:#60A5FA;--bl5:#3B82F6;--bl6:#1D4ED8;
  --gr4:#4ADE80;--gr5:#10B981;
  --am4:#FCD34D;--am5:#F59E0B;
  --rd4:#F87171;--rd5:#DC2626;
  --cy4:#22D3EE;--vt4:#A78BFA;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--t1);min-height:100vh;}
.noise{position:fixed;top:0;left:0;width:100%;height:100%;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");pointer-events:none;z-index:1;}
.glow{position:fixed;top:-200px;right:-200px;width:600px;height:600px;background:radial-gradient(circle,rgba(59,130,246,0.10) 0%,rgba(34,211,238,0.05) 40%,transparent 70%);pointer-events:none;}
.wrap{max-width:1440px;margin:0 auto;padding:24px;position:relative;z-index:2;}
header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:12px;}
h1{font-family:'Playfair Display',serif;font-size:28px;font-weight:800;background:linear-gradient(135deg,var(--bl3),var(--cy4));-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.sub{font-size:11px;color:var(--t3);margin-top:3px;letter-spacing:0.5px;text-transform:uppercase;}
.badges{display:flex;gap:6px;flex-wrap:wrap;align-items:center;}
.badge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:5px;font-size:11px;font-weight:500;background:var(--bg3);border:1px solid var(--bd);color:var(--t2);}
.badge.live{border-color:var(--bl5);color:var(--bl4);}
.badge.live::before{content:'';width:6px;height:6px;border-radius:50%;background:var(--bl4);animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}

.verdict{padding:16px 20px;border-radius:10px;margin-bottom:20px;background:linear-gradient(135deg,rgba(59,130,246,0.10),rgba(34,211,238,0.05));border:1px solid rgba(59,130,246,0.20);position:relative;}
.verdict h2{font-family:'Playfair Display',serif;font-size:20px;margin-bottom:6px;}
.verdict p{font-size:12.5px;color:var(--t2);line-height:1.65;}
.dev-block{float:right;text-align:right;margin-left:16px;}
.dev-big{font-family:'JetBrains Mono',monospace;font-size:48px;font-weight:700;line-height:1;}
.dev-label{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--t3);margin-top:4px;}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-bottom:20px;}
.card{padding:14px;border-radius:8px;background:var(--bg2);border:1px solid var(--bd2);}
.card .lbl{font-size:9px;text-transform:uppercase;letter-spacing:0.7px;color:var(--t3);font-weight:600;margin-bottom:5px;}
.card .val{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:600;}
.card .dt{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:500;margin-top:3px;padding:2px 5px;border-radius:3px;display:inline-block;}
.dh{background:rgba(220,38,38,0.15);color:var(--rd4);}
.dw{background:rgba(59,130,246,0.15);color:var(--bl4);}
.dn{background:rgba(148,163,184,0.15);color:var(--t2);}
.dg{background:rgba(16,185,129,0.15);color:var(--gr4);}
.da{background:rgba(245,158,11,0.15);color:var(--am4);}

.section{background:var(--bg2);border:1px solid var(--bd2);border-radius:10px;padding:16px;margin-bottom:16px;}
.section h3{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;margin-bottom:12px;}
.section-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;}
.chart-container{position:relative;width:100%;height:320px;}
.chart-container canvas{width:100%!important;height:100%!important;}
.chart-legend{display:flex;gap:12px;font-size:11px;flex-wrap:wrap;}
.chart-legend span{display:flex;align-items:center;gap:4px;color:var(--t2);}
.chart-legend .dot{width:9px;height:9px;border-radius:50%;}

.tbl-wrap{overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:11px;}
th{text-align:left;padding:6px 10px;font-size:8.5px;text-transform:uppercase;letter-spacing:0.6px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd2);background:var(--bg3);white-space:nowrap;position:sticky;top:0;cursor:pointer;user-select:none;}
th:hover{color:var(--t1);}
td{padding:7px 10px;border-bottom:1px solid var(--bd2);font-family:'JetBrains Mono',monospace;font-size:10.5px;white-space:nowrap;}
td.name{font-family:'DM Sans',sans-serif;font-weight:500;font-size:11px;}
tr:hover td{background:rgba(59,130,246,0.03);}

.pill{padding:3px 8px;border-radius:4px;font-size:9px;font-weight:700;letter-spacing:0.4px;text-transform:uppercase;}
.p-le{background:rgba(29,78,216,0.20);color:var(--bl3);}
.p-ex{background:rgba(59,130,246,0.18);color:var(--bl4);}
.p-nm{background:rgba(16,185,129,0.15);color:var(--gr4);}
.p-df{background:rgba(245,158,11,0.18);color:var(--am4);}
.p-ld{background:rgba(220,38,38,0.20);color:var(--rd4);}
.p-nd{background:rgba(148,163,184,0.10);color:var(--t3);}

.region-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:16px;}
.region-card{padding:16px;border-radius:10px;background:var(--bg2);border:1px solid var(--bd2);}
.region-name{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.7px;color:var(--t2);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bd2);display:flex;justify-content:space-between;align-items:center;}
.region-metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;}
.region-metric{padding:6px 0;}
.region-metric .lbl{font-size:8.5px;text-transform:uppercase;letter-spacing:0.5px;color:var(--t3);margin-bottom:2px;}
.region-metric .val{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:600;}
.region-cats{display:flex;gap:4px;flex-wrap:wrap;}
.region-cats .pill{font-size:8.5px;padding:2px 6px;}

.method{margin-top:10px;padding:14px;border-radius:8px;background:var(--bg3);font-size:10.5px;color:var(--t3);line-height:1.6;}
.method strong{color:var(--t2);}
.method code{color:var(--bl4);}
.method table{width:100%;font-size:10.5px;border-collapse:collapse;margin:6px 0;}
.method td{padding:5px 8px;font-family:'DM Sans',sans-serif;border-bottom:1px solid var(--bd2);}
.footer{text-align:center;padding:16px 0;font-size:9px;color:var(--t3);border-top:1px solid var(--bd2);margin-top:10px;}

.view-controls{display:flex;gap:2px;background:var(--bg3);padding:2px;border-radius:5px;border:1px solid var(--bd2);flex-wrap:wrap;}
.vbtn{padding:4px 10px;border-radius:3px;border:none;background:transparent;color:var(--t3);font-family:'DM Sans',sans-serif;font-size:10.5px;font-weight:500;cursor:pointer;transition:all 0.2s;}
.vbtn.active{background:var(--bl5);color:#F0F9FF;}
.vbtn:hover:not(.active){color:var(--t1);}

@media(max-width:768px){
  .wrap{padding:14px;}
  h1{font-size:22px;}
  .card .val{font-size:18px;}
  .dev-block{float:none;text-align:center;margin:0 0 10px 0;}
  .dev-big{font-size:38px;}
  .chart-container{height:230px;}
}
</style>
</head>
<body>
<div class="noise"></div><div class="glow"></div>
<div class="wrap">

  <header>
    <div>
      <h1>☔ Rain Monitor</h1>
      <div class="sub">India SW Monsoon — 36 IMD Subdivisions</div>
    </div>
    <div class="badges" id="badges"></div>
  </header>

  <div class="verdict" id="verdict"></div>

  <div class="cards" id="cards"></div>

  <div class="section" id="chartSection">
    <div class="section-header">
      <h3 id="chartTitle">Pan-India Cumulative Rainfall</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <div class="chart-legend" id="legend"></div>
        <div class="view-controls">
          <button class="vbtn active" onclick="switchChart('cum',this)">Cumulative</button>
          <button class="vbtn" onclick="switchChart('daily',this)">Daily</button>
        </div>
      </div>
    </div>
    <div class="chart-container"><canvas id="mainChart"></canvas></div>
  </div>

  <div class="section">
    <h3>Regional Breakdown — 4 Broad Homogeneous Regions</h3>
    <div class="region-grid" id="regionGrid"></div>
  </div>

  <div class="section">
    <div class="section-header">
      <h3>Subdivisions — Rainfall vs LPA</h3>
      <div class="view-controls">
        <button class="vbtn active" onclick="sortBy('region',this)">Region</button>
        <button class="vbtn" onclick="sortBy('dev',this)">IMD Departure</button>
        <button class="vbtn" onclick="sortBy('omDev',this)">OM Read</button>
        <button class="vbtn" onclick="sortBy('total',this)">Total mm</button>
        <button class="vbtn" onclick="sortBy('devPrev',this)">vs Last Year</button>
      </div>
    </div>
    <div class="tbl-wrap"><table>
      <thead id="thead"></thead>
      <tbody id="subTable"></tbody>
    </table></div>
  </div>

  <div class="section">
    <h3>Methodology & Caveats</h3>
    <div class="method" id="method"></div>
  </div>

  <div class="footer" id="footer"></div>

</div>

<script>
/*__DATA_BLOCK__*/

// ═══════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════
const D = DATA;
function fmtDev(v){if(v===null||v===undefined)return'—';const s=v>=0?'+':'';return s+v.toFixed(1)+'%';}
function fmtMM(v){if(v===null||v===undefined)return'—';return v.toFixed(0)+' mm';}
function catPill(cat){
  const cls = {'LARGE EXCESS':'p-le','EXCESS':'p-ex','NORMAL':'p-nm','DEFICIENT':'p-df','LARGE DEFICIENT':'p-ld','NO DATA':'p-nd'}[cat]||'p-nd';
  return `<span class="pill ${cls}">${cat}</span>`;
}
function devClass(v){
  if(v===null||v===undefined)return 'dn';
  if(v>=60) return 'dw';
  if(v>=20) return 'dw';
  if(v>=-19) return 'dg';
  if(v>=-59) return 'da';
  return 'dh';
}
function devColor(v){
  if(v===null||v===undefined)return 'var(--t3)';
  if(v>=60) return 'var(--bl3)';
  if(v>=20) return 'var(--bl4)';
  if(v>=-19) return 'var(--gr4)';
  if(v>=-59) return 'var(--am4)';
  return 'var(--rd4)';
}

// ═══════════════════════════════════════════════════════════════
// Badges — IMD primary, Open-Meteo secondary
// ═══════════════════════════════════════════════════════════════
const isImd = D.primarySource === 'IMD';
document.getElementById('badges').innerHTML = `
  <span class="badge live" style="border-color:${isImd?'var(--bl5)':'var(--am5)'};color:${isImd?'var(--bl4)':'var(--am4)'};">${isImd?'IMD Official':'Open-Meteo (IMD unreachable)'}</span>
  <span class="badge">${D.currentStart} → ${D.currentEnd}</span>
  <span class="badge">Day ${D.daysElapsed} of ${D.totalDays}</span>
  ${isImd?`<span class="badge">IMD wk end: ${D.imdLatestWeek||'—'}</span>`:''}
  <span class="badge">${D.subdivisions.length} subdivisions</span>
  <span class="badge">LPA: ${D.baselineLabel}</span>`;

// ═══════════════════════════════════════════════════════════════
// Verdict — headline driven by IMD (when available); OM shown for cross-check
// ═══════════════════════════════════════════════════════════════
const primaryDev = D.pan.primary_dev;
const primaryCat = D.pan.primary_cat;
const omDev      = D.pan.dev_vs_lpa;
const omCat      = D.pan.category;
const primaryFmt = fmtDev(primaryDev);
document.getElementById('verdict').innerHTML = `
  <div class="dev-block">
    <div class="dev-big" style="color:${D.verdictColor}">${primaryFmt}</div>
    <div class="dev-label">${isImd?`IMD cumul. as of ${D.imdLatestWeek||'—'}`:'vs '+D.baselineLabel+' LPA'}</div>
    ${isImd?`<div style="font-size:9.5px;color:var(--t3);margin-top:6px;line-height:1.3">Our OM read: <span style="color:${devColor(omDev)}">${fmtDev(omDev)}</span> · Δ ${fmtDev((primaryDev??0)-(omDev??0))}</div>`:''}
  </div>
  <h2 style="color:${D.verdictColor}">${D.verdictEmoji} ${D.verdictText}</h2>
  <p>
    ${isImd?`<strong>IMD official cumulative departure</strong> as of week ending ${D.imdLatestWeek||'—'}: <strong>${primaryFmt}</strong> (${primaryCat}).<br>`:''}
    Season-to-date (day ${D.daysElapsed} of ${D.totalDays}, Jun 1 → ${D.currentEnd}):
    Open-Meteo grid says <strong>${fmtMM(D.pan.cur_total)}</strong> vs 5yr proxy LPA <strong>${fmtMM(D.pan.lpa_todate)}</strong>
    (${fmtDev(omDev)}). vs ${D.prevYear}: <strong>${fmtDev(D.pan.dev_vs_prev)}</strong>.<br>
    ${isImd?'IMD subdivision distribution':'OM subdivision distribution'}:
    <span class="pill p-le">LE ${D.pan.cats['LARGE EXCESS']}</span>&nbsp;
    <span class="pill p-ex">EX ${D.pan.cats['EXCESS']}</span>&nbsp;
    <span class="pill p-nm">NM ${D.pan.cats['NORMAL']}</span>&nbsp;
    <span class="pill p-df">DF ${D.pan.cats['DEFICIENT']}</span>&nbsp;
    <span class="pill p-ld">LD ${D.pan.cats['LARGE DEFICIENT']}</span>
    &nbsp;of ${D.pan.n_subs} subdivisions${isImd?' (IMD matched: '+D.pan.imd_n+')':''}.
  </p>`;

// ═══════════════════════════════════════════════════════════════
// Cards — IMD headline, OM as complementary
// ═══════════════════════════════════════════════════════════════
document.getElementById('cards').innerHTML = `
  <div class="card" style="border-color:${devColor(primaryDev)};border-width:1.5px;">
    <div class="lbl">${isImd?'IMD Departure (Official)':'Deviation vs LPA'}</div>
    <div class="val" style="color:${devColor(primaryDev)}">${primaryFmt}</div>
    <div class="dt ${devClass(primaryDev)}">${primaryCat}</div></div>
  <div class="card"><div class="lbl">${isImd?'Our OM Read':'—'}</div>
    <div class="val" style="color:${devColor(omDev)};opacity:0.8">${fmtDev(omDev)}</div>
    <div class="dt ${devClass(omDev)}">${omCat}</div></div>
  <div class="card"><div class="lbl">Actual (Season-to-date)</div>
    <div class="val" style="color:var(--bl4)">${fmtMM(D.pan.cur_total)}</div>
    <div class="dt dn">Day ${D.daysElapsed}/${D.totalDays}</div></div>
  <div class="card"><div class="lbl">LPA proxy (Same Window)</div>
    <div class="val" style="color:var(--t2)">${fmtMM(D.pan.lpa_todate)}</div>
    <div class="dt dn">${D.baselineLabel}</div></div>
  <div class="card"><div class="lbl">vs ${D.prevYear}</div>
    <div class="val" style="color:${devColor(D.pan.dev_vs_prev)}">${fmtDev(D.pan.dev_vs_prev)}</div>
    <div class="dt dn">Prev: ${fmtMM(D.pan.prev_total)}</div></div>
  <div class="card"><div class="lbl">Above-Normal Subs</div>
    <div class="val" style="color:var(--bl4)">${(D.pan.cats['LARGE EXCESS']+D.pan.cats['EXCESS'])}</div>
    <div class="dt dw">of ${D.pan.n_subs}</div></div>
  <div class="card"><div class="lbl">Deficient Subs</div>
    <div class="val" style="color:var(--am4)">${(D.pan.cats['DEFICIENT']+D.pan.cats['LARGE DEFICIENT'])}</div>
    <div class="dt da">of ${D.pan.n_subs}</div></div>`;

// ═══════════════════════════════════════════════════════════════
// Chart
// ═══════════════════════════════════════════════════════════════
let currentView = 'cum';
const legends = {
  cum: `<span><span class="dot" style="background:var(--bl4)"></span>${D.currentYear}</span>
        <span><span class="dot" style="background:var(--t2)"></span>${D.prevYear}</span>
        <span><span class="dot" style="background:var(--gr4);opacity:0.7"></span>LPA (${D.baselineLabel})</span>`,
  daily: `<span><span class="dot" style="background:var(--bl4)"></span>${D.currentYear} daily</span>
          <span><span class="dot" style="background:var(--t2)"></span>${D.prevYear} daily</span>`,
};
document.getElementById('legend').innerHTML = legends.cum;
const titles = { cum:'Pan-India Cumulative Rainfall — Jun 1 onward', daily:'Pan-India Daily Rainfall' };

function drawChart(view){
  const canvas = document.getElementById('mainChart');
  const ctx = canvas.getContext('2d');
  const W = canvas.parentElement.clientWidth, H = canvas.parentElement.clientHeight;
  canvas.width = W*2; canvas.height = H*2; ctx.scale(2,2);
  const pad = {top:20,right:20,bottom:40,left:60};
  ctx.clearRect(0,0,W,H);

  let curr, prev, lpa;
  if(view === 'cum'){
    curr = D.pan.cur_cum; prev = D.pan.prev_cum; lpa = D.pan.lpa_cum;
  } else {
    // daily = differences of cumulative
    const diff = arr => arr.map((v,i)=> i===0 ? v : (v!==null && arr[i-1]!==null ? Math.max(0, v-arr[i-1]) : null));
    curr = diff(D.pan.cur_cum); prev = diff(D.pan.prev_cum); lpa = null;
  }

  const all = [...(curr||[]),...(prev||[]),...(lpa||[])].filter(v=>v!==null && v!==undefined);
  if(!all.length){ ctx.fillStyle='var(--t3)';ctx.font='12px DM Sans';ctx.textAlign='center';ctx.fillText('No data yet',W/2,H/2); return; }
  const yMin = 0;
  const yMax = Math.ceil(Math.max(...all) * 1.1);
  const pW = W-pad.left-pad.right, pH = H-pad.top-pad.bottom;
  const maxD = Math.max((curr||[]).length,(prev||[]).length,(lpa||[]).length);
  const xS = i => pad.left + (i/Math.max(maxD-1,1))*pW;
  const yS = v => pad.top + pH - ((v-yMin)/(yMax-yMin))*pH;

  // Grid
  ctx.strokeStyle='rgba(255,255,255,0.05)';ctx.lineWidth=0.5;
  for(let i=0;i<=5;i++){
    const val = yMin + (i/5)*(yMax-yMin), y = yS(val);
    ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(W-pad.right,y); ctx.stroke();
    ctx.fillStyle='rgba(255,255,255,0.35)'; ctx.font='10px JetBrains Mono'; ctx.textAlign='right';
    ctx.fillText(val.toFixed(0)+' mm',pad.left-8,y+3);
  }

  // X labels — month markers Jun/Jul/Aug/Sep
  ctx.fillStyle='rgba(255,255,255,0.35)';ctx.font='10px JetBrains Mono';ctx.textAlign='center';
  const monthDays = [0,30,61,92,122]; // Jun 1, Jul 1, Aug 1, Sep 1, Sep 30
  const monthLbls = ['Jun','Jul','Aug','Sep','end'];
  monthDays.forEach((d,i)=>{ if(d<maxD) ctx.fillText(monthLbls[i], xS(d), H-pad.bottom+20); });

  function drawLine(arr,color,width,dash,fillGrad){
    if(!arr||!arr.length)return;
    if(fillGrad){
      const g = ctx.createLinearGradient(0, pad.top, 0, pad.top+pH);
      g.addColorStop(0, fillGrad); g.addColorStop(1, 'rgba(59,130,246,0)');
      ctx.fillStyle = g; ctx.beginPath();
      ctx.moveTo(xS(0), pad.top+pH);
      arr.forEach((v,i)=>{ if(v!==null && v!==undefined) ctx.lineTo(xS(i), yS(v)); });
      ctx.lineTo(xS(arr.length-1), pad.top+pH); ctx.closePath(); ctx.fill();
    }
    ctx.strokeStyle=color; ctx.lineWidth=width; if(dash) ctx.setLineDash(dash);
    ctx.beginPath();
    arr.forEach((v,i)=>{ if(v===null||v===undefined)return; const x=xS(i), y=yS(v); i===0||arr[i-1]===null?ctx.moveTo(x,y):ctx.lineTo(x,y); });
    ctx.stroke(); if(dash) ctx.setLineDash([]);
  }

  if(lpa) drawLine(lpa, 'rgba(74,222,128,0.55)', 2, [6,3], null);
  if(prev) drawLine(prev, 'rgba(148,163,184,0.6)', 1.5, [4,4], null);
  if(curr){
    drawLine(curr, '#60A5FA', 2.5, null, 'rgba(59,130,246,0.15)');
    const last = curr.length-1;
    if(curr[last]!==null && curr[last]!==undefined){
      ctx.beginPath(); ctx.arc(xS(last), yS(curr[last]), 4.5, 0, Math.PI*2);
      ctx.fillStyle='#60A5FA'; ctx.fill();
      ctx.strokeStyle='var(--bg)'; ctx.lineWidth=2; ctx.stroke();
    }
  }
}

function switchChart(v,btn){
  currentView = v;
  document.querySelectorAll('#chartSection .vbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('chartTitle').textContent = titles[v]||'';
  document.getElementById('legend').innerHTML = legends[v]||legends.cum;
  drawChart(v);
}

drawChart('cum');
window.addEventListener('resize', ()=>drawChart(currentView));

// ═══════════════════════════════════════════════════════════════
// Regional cards
// ═══════════════════════════════════════════════════════════════
const regionHTML = ['NW','C','S','ENE'].map(r=>{
  const rd = D.regional[r]; if(!rd) return '';
  const rn = D.regionNames[r];
  const pDev = rd.primary_dev;
  const pCat = rd.primary_cat;
  const oDev = rd.dev_vs_lpa;
  return `<div class="region-card">
    <div class="region-name">
      <span>${rn}</span>
      ${catPill(pCat)}
    </div>
    <div class="region-metrics">
      <div class="region-metric"><div class="lbl">IMD Departure</div><div class="val" style="color:${devColor(pDev)}">${fmtDev(pDev)}</div></div>
      <div class="region-metric"><div class="lbl">Our OM Read</div><div class="val" style="color:${devColor(oDev)};opacity:0.75">${fmtDev(oDev)}</div></div>
      <div class="region-metric"><div class="lbl">Actual (mm)</div><div class="val" style="color:var(--bl4)">${fmtMM(rd.cur_total)}</div></div>
      <div class="region-metric"><div class="lbl">vs ${D.prevYear}</div><div class="val" style="color:${devColor(rd.dev_vs_prev)}">${fmtDev(rd.dev_vs_prev)}</div></div>
    </div>
    <div class="region-cats">
      ${rd.cats['LARGE EXCESS']?`<span class="pill p-le">LE ${rd.cats['LARGE EXCESS']}</span>`:''}
      ${rd.cats['EXCESS']?`<span class="pill p-ex">EX ${rd.cats['EXCESS']}</span>`:''}
      ${rd.cats['NORMAL']?`<span class="pill p-nm">NM ${rd.cats['NORMAL']}</span>`:''}
      ${rd.cats['DEFICIENT']?`<span class="pill p-df">DF ${rd.cats['DEFICIENT']}</span>`:''}
      ${rd.cats['LARGE DEFICIENT']?`<span class="pill p-ld">LD ${rd.cats['LARGE DEFICIENT']}</span>`:''}
    </div>
  </div>`;
}).join('');
document.getElementById('regionGrid').innerHTML = regionHTML;

// ═══════════════════════════════════════════════════════════════
// Subdivision table
// ═══════════════════════════════════════════════════════════════
let currentSort = 'region';
document.getElementById('thead').innerHTML = `<tr>
  <th>Subdivision</th><th>Region</th>
  <th>IMD Departure</th><th>Category (IMD)</th>
  <th>Our OM Read</th><th>Actual (mm)</th>
  <th>LPA proxy (mm)</th><th>vs ${D.prevYear}</th></tr>`;

function renderTable(){
  let s = [...D.subdivisions];
  if(currentSort === 'region')       s.sort((a,b)=> a.region.localeCompare(b.region) || ((a.primaryDev??-999) - (b.primaryDev??-999)));
  else if(currentSort === 'dev')     s.sort((a,b)=> (b.primaryDev??-999) - (a.primaryDev??-999));
  else if(currentSort === 'total')   s.sort((a,b)=> (b.curTotal||0) - (a.curTotal||0));
  else if(currentSort === 'devPrev') s.sort((a,b)=> (b.devVsPrev??-999) - (a.devVsPrev??-999));
  else if(currentSort === 'omDev')   s.sort((a,b)=> (b.devVsLpa??-999) - (a.devVsLpa??-999));

  document.getElementById('subTable').innerHTML = s.map(sub=>{
    const pDev = sub.primaryDev;
    const pCat = sub.primaryCat;
    const oDev = sub.devVsLpa;
    return `<tr>
    <td class="name">${sub.name}</td>
    <td style="color:var(--t3);font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;">${sub.region}</td>
    <td style="color:${devColor(pDev)};font-weight:700">${fmtDev(pDev)}</td>
    <td>${catPill(pCat)}</td>
    <td style="color:${devColor(oDev)};opacity:0.75">${fmtDev(oDev)}</td>
    <td style="color:var(--bl4);font-weight:600">${fmtMM(sub.curTotal)}</td>
    <td style="color:var(--t2)">${fmtMM(sub.lpaTodate)}</td>
    <td style="color:${devColor(sub.devVsPrev)}">${fmtDev(sub.devVsPrev)}</td>
  </tr>`;}).join('');
}
function sortBy(k, btn){
  currentSort = k;
  btn.parentElement.querySelectorAll('.vbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  renderTable();
}
renderTable();

// ═══════════════════════════════════════════════════════════════
// Methodology
// ═══════════════════════════════════════════════════════════════
document.getElementById('method').innerHTML = `
  <strong style="color:var(--t1);font-size:12px">━━ DATA SOURCES ━━</strong><br><br>
  <strong>Primary (headline verdict, category, cards, table):</strong> Official IMD cumulative % departure
  scraped from <code>imdpune.gov.in/seasons/cumulative.html</code>. IMD publishes this weekly (typically Thursday),
  computed from hundreds of surface stations per subdivision, area-weighted, and compared to IMD's 1971-2020 LPA.
  This is the number news outlets and agri-analysts cite. Latest week loaded: <strong>${D.imdLatestWeek||'—'}</strong>.<br><br>
  <strong>Secondary (mm actuals, cumulative chart, daily granularity):</strong> Open-Meteo daily precipitation
  from 109 representative points (2-4 per subdivision) blended with a 5yr (${D.baselineLabel.replace(' (5yr)','')}) LPA proxy.
  Useful for daily/weekly tracking between IMD's weekly updates and for absolute mm figures IMD doesn't publish for
  subdivisions in the cumulative table. Cross-check with IMD reveals mean absolute error of ~28pp — trust IMD's number
  when they disagree.<br><br>
  If IMD is unreachable at fetch time, the dashboard automatically falls back to Open-Meteo-only mode and the badge
  above the header changes color to flag this.<br><br>

  <strong style="color:var(--t1);font-size:12px">━━ WINDOW ━━</strong><br><br>
  Southwest Monsoon: <strong>June 1 – September 30</strong>. Currently on <strong>day ${D.daysElapsed} of ${D.totalDays}</strong>
  (Jun 1 → ${D.currentEnd}). All comparisons are same-window: current year through today vs the same calendar range
  in ${D.prevYear} and in each of the 5 baseline years (${D.baselineLabel.replace(' (5yr)','')}).<br><br>

  <strong style="color:var(--t1);font-size:12px">━━ IMD DEFICIT/SURPLUS CATEGORIES ━━</strong><br><br>
  Deviation from LPA (%) drives the category label — matches the standard IMD framework used in weekly rainfall bulletins.
  <table>
    <tr><td style="width:30%"><span class="pill p-le">LARGE EXCESS</span></td><td>≥ +60% above LPA</td></tr>
    <tr><td><span class="pill p-ex">EXCESS</span></td><td>+20% to +59%</td></tr>
    <tr><td><span class="pill p-nm">NORMAL</span></td><td>−19% to +19%</td></tr>
    <tr><td><span class="pill p-df">DEFICIENT</span></td><td>−20% to −59%</td></tr>
    <tr><td><span class="pill p-ld">LARGE DEFICIENT</span></td><td>≤ −60% below LPA</td></tr>
  </table><br>

  <strong style="color:var(--t1);font-size:12px">━━ LPA (LONG PERIOD AVERAGE) ━━</strong><br><br>
  IMD's official LPA is the 1971–2020 mean (50 years). We use a <strong>5-year mean (${D.baselineLabel.replace(' (5yr)','')})</strong>
  as a proxy — more responsive to recent climate but not directly comparable to IMD's absolute LPA figures.
  Deviation direction and magnitude are directionally consistent with IMD when relative signals matter (better/worse than average).<br><br>

  <strong style="color:var(--t1);font-size:12px">━━ DATA SOURCE ━━</strong><br><br>
  Open-Meteo daily precipitation (ERA5 reanalysis + hi-res regional models, ~14 km grid).
  Each of the 36 subdivisions is represented by 2–4 lat/lon points averaged for the subdivision total.
  Points chosen for geographic spread (state capital + major districts).<br><br>

  <strong style="color:var(--t1);font-size:12px">━━ CAVEATS ━━</strong><br><br>
  • <strong>Grid vs station:</strong> Open-Meteo grid reads may differ from IMD station observations by ±10-20% in absolute mm.
  Same grid cells are compared across years so <strong>relative signals (vs LPA, YoY) are internally consistent.</strong><br>
  • <strong>Not area-weighted:</strong> Subdivision aggregate is a simple mean of representative points — IMD uses full district area weighting.<br>
  • <strong>5yr LPA proxy:</strong> May underestimate the 50yr LPA in wet climate periods and overestimate in dry ones.
  Use for direction, not absolute claims.<br>
  • <strong>Small subdivisions:</strong> Lakshadweep, A&N have only 2 points and are noisier.<br>
`;

// ═══════════════════════════════════════════════════════════════
// Footer
// ═══════════════════════════════════════════════════════════════
document.getElementById('footer').innerHTML =
  `Rain Monitor v2 — Generated ${D.generatedAt} — IMD Pune (primary) + Open-Meteo (secondary, CC BY 4.0) — Anand Consumer Research`;
</script>
</body>
</html>
'''


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("Rain Monitor — India SW Monsoon Tracker", file=sys.stderr)
    print("=" * 55, file=sys.stderr)

    dates = get_date_ranges()
    print(f"Current window : {dates['current_start']} → {dates['current_end']} (day {dates['days_elapsed']}/{dates['total_days']})", file=sys.stderr)
    print(f"Prev year      : {dates['prev_start']} → {dates['prev_end']}", file=sys.stderr)
    print(f"Baseline (LPA) : {dates['baseline_label']}", file=sys.stderr)
    print(f"Subdivisions   : {len(SUBDIVISIONS)}", file=sys.stderr)
    print("", file=sys.stderr)

    print("Fetching IMD official data (primary signal)...", file=sys.stderr)
    imd_data = fetch_imd_official()

    print("\nFetching Open-Meteo precipitation data (secondary, for daily granularity)...", file=sys.stderr)
    all_data = fetch_all(dates)

    print("\nComputing stats per subdivision...", file=sys.stderr)
    stats = [compute_subdivision_stats(sd, dates, imd_data) for sd in all_data]

    print("Aggregating regional + pan-India...", file=sys.stderr)
    regional, pan = compute_regional_and_pan(stats)

    print(f"\nPan-India summary:", file=sys.stderr)
    print(f"  IMD official (primary): {pan.get('imd_dev')}% ({pan.get('imd_cat')}) — {pan.get('imd_n',0)}/36 subs", file=sys.stderr)
    print(f"  Open-Meteo derived    : {pan['dev_vs_lpa']}%  ({pan['category']})", file=sys.stderr)
    print(f"  Actual (S-T-D): {pan['cur_total']} mm", file=sys.stderr)
    print(f"  LPA            : {pan['lpa_todate']} mm", file=sys.stderr)
    print(f"  Dev vs LPA     : {pan['dev_vs_lpa']}%  → {pan['category']}", file=sys.stderr)
    print(f"  vs {dates['prev_year']}      : {pan['dev_vs_prev']}%", file=sys.stderr)
    print(f"  Categories     : {pan['cats']}", file=sys.stderr)

    print("\nGenerating HTML...", file=sys.stderr)
    html = generate_html(stats, regional, pan, dates)
    out_path = Path(__file__).parent / "rain_monsoon_monitor.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote: {out_path}  ({len(html):,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
