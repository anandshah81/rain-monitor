#!/usr/bin/env python3
"""
Rain Monitor v3 — India SW Monsoon Tracker (IMD-only)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pulls the official IMD cumulative % departure from LPA for all 36
meteorological subdivisions from imdpune.gov.in and generates a dashboard.

Weekly cadence — IMD updates on Thursdays.
No Open-Meteo, no absolute mm figures (IMD publishes only % departure).

Run:  python rain_monitor.py
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

IMD_URL_CUMULATIVE = "https://imdpune.gov.in/seasons/cumulative.html"
IMD_URL_WEEKBYWEEK = "https://imdpune.gov.in/seasons/weekbyweek.html"

IMD_WEEK_LABELS = [
    "Jun 3","Jun 10","Jun 17","Jun 24",
    "Jul 1","Jul 8","Jul 15","Jul 22","Jul 29",
    "Aug 5","Aug 12","Aug 19","Aug 26",
    "Sep 2","Sep 9","Sep 16","Sep 23","Sep 30",
]

REGION_NAMES = {
    "NW":  "Northwest India",
    "C":   "Central India",
    "S":   "South Peninsula",
    "ENE": "East & Northeast India",
}

# All 36 IMD subdivisions with (our_name, imd_name, region).
# imd_name must exactly match the string in imdpune.gov.in's HTML table.
SUBDIVISIONS = [
    # ─── Northwest India (9) ────────────────────────────────────
    ("Jammu, Kashmir & Ladakh",             "Jammu & Kashmir and Ladakh", "NW"),
    ("Himachal Pradesh",                    "Himachal Pradesh",           "NW"),
    ("Punjab",                              "Punjab",                     "NW"),
    ("Haryana, Chandigarh & Delhi",         "Har. Chd. & Delhi",          "NW"),
    ("Uttarakhand",                         "Uttarakhand",                "NW"),
    ("West Uttar Pradesh",                  "West Uttar Pradesh",         "NW"),
    ("East Uttar Pradesh",                  "East Uttar Pradesh",         "NW"),
    ("West Rajasthan",                      "West Rajasthan",             "NW"),
    ("East Rajasthan",                      "East Rajasthan",             "NW"),
    # ─── Central India (10) ─────────────────────────────────────
    ("West Madhya Pradesh",                 "West Madhya Pradesh",        "C"),
    ("East Madhya Pradesh",                 "East Madhya Pradesh",        "C"),
    ("Vidarbha",                            "Vidarbha",                   "C"),
    ("Chhattisgarh",                        "Chhattisgarh",               "C"),
    ("Gujarat Region",                      "Gujarat Region",             "C"),
    ("Saurashtra & Kutch",                  "Saurashtra & Kutch",         "C"),
    ("Konkan & Goa",                        "Konkan & Goa",               "C"),
    ("Madhya Maharashtra",                  "Madhya Maharashtra",         "C"),
    ("Marathwada",                          "Marathwada",                 "C"),
    ("Odisha",                              "Odisha",                     "C"),
    # ─── South Peninsula (10) ───────────────────────────────────
    ("Coastal Andhra Pradesh",              "Coastal AP and Yanam",       "S"),
    ("Telangana",                           "Telangana",                  "S"),
    ("Rayalaseema",                         "Rayalaseema",                "S"),
    ("Tamil Nadu, Puducherry & Karaikal",   "TN. Pudu.and Karaikal",      "S"),
    ("Coastal Karnataka",                   "Coastal Karnataka",          "S"),
    ("North Interior Karnataka",            "N. I. Karnataka",            "S"),
    ("South Interior Karnataka",            "S. I. Karnataka",            "S"),
    ("Kerala & Mahe",                       "Kerala & Mahe",              "S"),
    ("Lakshadweep",                         "Lakshdweep",                 "S"),
    ("Andaman & Nicobar Islands",           "A & N Islands",              "S"),
    # ─── East & Northeast India (7) ─────────────────────────────
    ("Bihar",                               "Bihar",                      "ENE"),
    ("Jharkhand",                           "Jharkhand",                  "ENE"),
    ("Gangetic West Bengal",                "Gangetic West Bengal",       "ENE"),
    ("Sub-Himalayan West Bengal & Sikkim",  "SHWB & Sikkim",              "ENE"),
    ("Arunachal Pradesh",                   "Arunachal Pradesh",          "ENE"),
    ("Assam & Meghalaya",                   "Assam & Meghalaya",          "ENE"),
    ("Nagaland, Manipur, Mizoram & Tripura","N M M T",                    "ENE"),
]
assert len(SUBDIVISIONS) == 36, f"Expected 36, got {len(SUBDIVISIONS)}"


# IMD deficit categories (standard)
def category(pct):
    if pct is None: return "NO DATA"
    if pct >= 60:   return "LARGE EXCESS"
    if pct >= 20:   return "EXCESS"
    if pct >= -19:  return "NORMAL"
    if pct >= -59:  return "DEFICIENT"
    return "LARGE DEFICIENT"


# ═══════════════════════════════════════════════════════════════
# IMD SCRAPER
# ═══════════════════════════════════════════════════════════════

def _parse_imd_table(html):
    """Parse imdpune.gov.in cumulative.html or weekbyweek.html.
    Returns {imd_name: {week_label: dev_pct}}"""
    result = {}
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    for tr in trs[3:]:  # skip 3 header rows
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL | re.IGNORECASE)
        cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', c)).strip() for c in cells]
        if len(cells) < 3:
            continue
        try:
            int(cells[0])          # Sr No must be an integer
            name = cells[1]
        except (ValueError, IndexError):
            continue
        weeks = cells[2:]
        wk_data = {}
        for wl, val in zip(IMD_WEEK_LABELS, weeks):
            if val:
                try:
                    v = float(val)
                    # IMD uses 0.0 as a sentinel for "missing this week"; treat as None
                    if abs(v) > 0.01:
                        wk_data[wl] = v
                except ValueError:
                    pass
        result[name] = wk_data
    return result


def fetch_imd():
    """Fetch IMD cumulative + weekly departures. Raises if unreachable."""
    print(f"Fetching IMD cumulative from {IMD_URL_CUMULATIVE}", file=sys.stderr)
    cum = requests.get(IMD_URL_CUMULATIVE, timeout=30).text
    print(f"Fetching IMD weekly     from {IMD_URL_WEEKBYWEEK}", file=sys.stderr)
    wkw = requests.get(IMD_URL_WEEKBYWEEK, timeout=30).text
    cum_data = _parse_imd_table(cum)
    wkw_data = _parse_imd_table(wkw)
    print(f"  parsed {len(cum_data)} cumulative rows, {len(wkw_data)} weekly rows", file=sys.stderr)
    return cum_data, wkw_data


# ═══════════════════════════════════════════════════════════════
# COMPUTE
# ═══════════════════════════════════════════════════════════════

def compute(cum_data, wkw_data):
    """Turn parsed IMD tables into stats per subdivision + regional/pan aggregates."""
    stats = []
    unmatched = []
    for our_name, imd_name, region in SUBDIVISIONS:
        cum_weeks = cum_data.get(imd_name, {})
        wkw_weeks = wkw_data.get(imd_name, {})
        if not cum_weeks:
            unmatched.append((our_name, imd_name))
        # Latest cumulative week
        latest_week = None
        latest_dev = None
        for wl in IMD_WEEK_LABELS:
            if wl in cum_weeks:
                latest_week = wl
                latest_dev = cum_weeks[wl]
        # Week-over-week change: latest weekly departure - previous weekly departure
        prev_week = None
        prev_dev = None
        if latest_week and latest_week in IMD_WEEK_LABELS:
            idx = IMD_WEEK_LABELS.index(latest_week)
            for i in range(idx - 1, -1, -1):
                pw = IMD_WEEK_LABELS[i]
                if pw in cum_weeks:
                    prev_week = pw
                    prev_dev = cum_weeks[pw]
                    break
        wow_change = None
        if latest_dev is not None and prev_dev is not None:
            wow_change = round(latest_dev - prev_dev, 1)
        stats.append({
            "name":         our_name,
            "imd_name":     imd_name,
            "region":       region,
            "latest_week":  latest_week,
            "dev":          latest_dev,
            "category":     category(latest_dev),
            "prev_week":    prev_week,
            "prev_dev":     prev_dev,
            "wow_change":   wow_change,
            "cum_weeks":    cum_weeks,
            "wkw_weeks":    wkw_weeks,
        })

    if unmatched:
        print(f"⚠ Unmatched subdivisions (IMD name mismatch): {len(unmatched)}", file=sys.stderr)
        for u in unmatched:
            print(f"    {u[0]}  →  looked for {u[1]!r}", file=sys.stderr)

    # Regional & pan aggregates (simple mean of subdivision-level dev%)
    def _agg(subset):
        vals = [s["dev"] for s in subset if s["dev"] is not None]
        if not vals: return None
        avg = round(sum(vals) / len(vals), 1)
        cats = {"LARGE EXCESS":0,"EXCESS":0,"NORMAL":0,"DEFICIENT":0,"LARGE DEFICIENT":0,"NO DATA":0}
        for s in subset:
            cats[s["category"]] = cats.get(s["category"], 0) + 1
        wow_vals = [s["wow_change"] for s in subset if s["wow_change"] is not None]
        wow_avg = round(sum(wow_vals)/len(wow_vals), 1) if wow_vals else None
        return {
            "dev":      avg,
            "category": category(avg),
            "n_subs":   len(subset),
            "n_matched":len(vals),
            "cats":     cats,
            "wow_change": wow_avg,
        }
    regional = {r: _agg([s for s in stats if s["region"] == r]) for r in ["NW","C","S","ENE"]}
    pan = _agg(stats)
    # Find the latest week seen anywhere (should be same across all subs)
    all_weeks = [s["latest_week"] for s in stats if s["latest_week"]]
    latest_week_overall = None
    for wl in reversed(IMD_WEEK_LABELS):
        if wl in all_weeks:
            latest_week_overall = wl
            break
    pan["latest_week"] = latest_week_overall
    return stats, regional, pan


# ═══════════════════════════════════════════════════════════════
# HTML GENERATION
# ═══════════════════════════════════════════════════════════════

def _verdict(cat, dev):
    if   cat == "LARGE EXCESS":    return "💧💧", "Large Excess Rainfall — Flood Risk, Bullish for Kharif/Rural", "#1D4ED8"
    elif cat == "EXCESS":          return "💧",   "Excess Rainfall — Kharif Positive, Watch Reservoirs",           "#3B82F6"
    elif cat == "NORMAL":          return "☔",   "Normal Monsoon — In-Line with LPA",                              "#10B981"
    elif cat == "DEFICIENT":       return "☀️",   "Deficient Rainfall — Kharif Risk, Watch Reservoir Fill",         "#F59E0B"
    elif cat == "LARGE DEFICIENT": return "🌵",   "Large Deficient — Drought Watch, Cautious on Rural Consumption", "#DC2626"
    return "❓", "Insufficient IMD Data", "#78716C"


def generate_html(stats, regional, pan):
    dev = pan["dev"] if pan and pan.get("dev") is not None else 0
    cat = pan["category"] if pan else "NO DATA"
    ve, vt, vc = _verdict(cat, dev)

    # Sort subdivisions worst-first by region, then by dev%
    sorted_stats = sorted(stats, key=lambda s: (s["region"], s["dev"] if s["dev"] is not None else 999))

    payload = {
        "generatedAt":  datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "latestWeek":   pan.get("latest_week") if pan else None,
        "verdictEmoji": ve,
        "verdictText":  vt,
        "verdictColor": vc,
        "pan": pan,
        "regional": regional,
        "regionNames": REGION_NAMES,
        "subdivisions": [{
            "name":       s["name"],
            "region":     s["region"],
            "dev":        s["dev"],
            "category":   s["category"],
            "latestWeek": s["latest_week"],
            "wowChange":  s["wow_change"],
            "prevDev":    s["prev_dev"],
            "cumWeeks":   s["cum_weeks"],
        } for s in sorted_stats],
        "weekLabels": IMD_WEEK_LABELS,
    }

    tp = Path(__file__).parent / "rain_monitor_template.html"
    html = tp.read_text(encoding="utf-8") if tp.exists() else _template()
    return html.replace("/*__DATA_BLOCK__*/", "const DATA = " + json.dumps(payload) + ";")


def _template():
    return r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rain Monitor — India SW Monsoon (IMD Official)</title>

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
  --bg:#0A0E1A;--bg2:#111827;--bg3:#1E293B;--bd:#334155;--bd2:#1E293B;
  --t1:#F1F5F9;--t2:#94A3B8;--t3:#64748B;
  --bl3:#93C5FD;--bl4:#60A5FA;--bl5:#3B82F6;--bl6:#1D4ED8;
  --gr4:#4ADE80;--gr5:#10B981; --am4:#FCD34D;--am5:#F59E0B;
  --rd4:#F87171;--rd5:#DC2626; --cy4:#22D3EE;
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

.verdict{padding:18px 22px;border-radius:10px;margin-bottom:20px;background:linear-gradient(135deg,rgba(59,130,246,0.10),rgba(34,211,238,0.05));border:1px solid rgba(59,130,246,0.20);position:relative;}
.verdict h2{font-family:'Playfair Display',serif;font-size:22px;margin-bottom:8px;}
.verdict p{font-size:13px;color:var(--t2);line-height:1.65;}
.dev-block{float:right;text-align:right;margin-left:16px;}
.dev-big{font-family:'JetBrains Mono',monospace;font-size:54px;font-weight:700;line-height:1;}
.dev-sub{font-family:'JetBrains Mono',monospace;font-size:14px;color:var(--t3);margin-top:4px;}
.dev-label{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--t3);margin-top:6px;}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;margin-bottom:20px;}
.card{padding:16px;border-radius:8px;background:var(--bg2);border:1px solid var(--bd2);}
.card .lbl{font-size:9.5px;text-transform:uppercase;letter-spacing:0.7px;color:var(--t3);font-weight:600;margin-bottom:5px;}
.card .val{font-family:'JetBrains Mono',monospace;font-size:26px;font-weight:600;}
.card .dt{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:500;margin-top:4px;padding:2px 6px;border-radius:3px;display:inline-block;}
.dh{background:rgba(220,38,38,0.15);color:var(--rd4);}
.dw{background:rgba(59,130,246,0.15);color:var(--bl4);}
.dn{background:rgba(148,163,184,0.15);color:var(--t2);}
.dg{background:rgba(16,185,129,0.15);color:var(--gr4);}
.da{background:rgba(245,158,11,0.15);color:var(--am4);}

.section{background:var(--bg2);border:1px solid var(--bd2);border-radius:10px;padding:18px;margin-bottom:16px;}
.section h3{font-family:'Playfair Display',serif;font-size:17px;font-weight:700;margin-bottom:14px;}
.section-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px;}

.tbl-wrap{overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:11.5px;}
th{text-align:left;padding:7px 12px;font-size:9px;text-transform:uppercase;letter-spacing:0.6px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd2);background:var(--bg3);white-space:nowrap;position:sticky;top:0;cursor:pointer;user-select:none;}
th:hover{color:var(--t1);}
td{padding:8px 12px;border-bottom:1px solid var(--bd2);font-family:'JetBrains Mono',monospace;font-size:11px;white-space:nowrap;}
td.name{font-family:'DM Sans',sans-serif;font-weight:500;font-size:11.5px;}
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
.region-name{font-size:11.5px;font-weight:600;text-transform:uppercase;letter-spacing:0.7px;color:var(--t2);margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bd2);display:flex;justify-content:space-between;align-items:center;gap:10px;}
.region-metrics{display:flex;justify-content:space-between;gap:10px;margin-bottom:10px;}
.region-metric{flex:1;padding:6px 0;}
.region-metric .lbl{font-size:9px;text-transform:uppercase;letter-spacing:0.5px;color:var(--t3);margin-bottom:3px;}
.region-metric .val{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:600;}
.region-cats{display:flex;gap:4px;flex-wrap:wrap;}
.region-cats .pill{font-size:8.5px;padding:2px 6px;}

.method{margin-top:6px;padding:16px;border-radius:8px;background:var(--bg3);font-size:11px;color:var(--t3);line-height:1.65;}
.method strong{color:var(--t2);}.method code{color:var(--bl4);}
.method table{width:100%;font-size:11px;border-collapse:collapse;margin:6px 0;}
.method td{padding:5px 8px;font-family:'DM Sans',sans-serif;border-bottom:1px solid var(--bd2);}
.footer{text-align:center;padding:16px 0;font-size:9.5px;color:var(--t3);border-top:1px solid var(--bd2);margin-top:10px;}

@media(max-width:768px){
  .wrap{padding:14px;}
  h1{font-size:22px;}
  .card .val{font-size:20px;}
  .dev-block{float:none;text-align:center;margin:0 0 10px 0;}
  .dev-big{font-size:44px;}
}
</style>
</head>
<body>
<div class="noise"></div><div class="glow"></div>
<div class="wrap">

  <header>
    <div>
      <h1>☔ Rain Monitor</h1>
      <div class="sub">India SW Monsoon — Official IMD Data</div>
    </div>
    <div class="badges" id="badges"></div>
  </header>

  <div class="verdict" id="verdict"></div>

  <div class="cards" id="cards"></div>

  <div class="section">
    <h3>Regional Breakdown — 4 Broad Homogeneous Regions</h3>
    <div class="region-grid" id="regionGrid"></div>
  </div>

  <div class="section">
    <div class="section-header">
      <h3>Subdivisions — Cumulative Departure vs LPA</h3>
      <div style="display:flex;gap:2px;background:var(--bg3);padding:2px;border-radius:5px;border:1px solid var(--bd2);">
        <button class="sort-btn active" data-sort="region">Region</button>
        <button class="sort-btn" data-sort="dev">Departure</button>
        <button class="sort-btn" data-sort="wow">Weekly Δ</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead id="thead"></thead>
        <tbody id="subTable"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <h3>Methodology & Sources</h3>
    <div class="method" id="method"></div>
  </div>

  <div class="footer" id="footer"></div>

</div>

<script>
/*__DATA_BLOCK__*/

const D = DATA;

function fmtDev(v){if(v===null||v===undefined)return'—';const s=v>=0?'+':'';return s+v.toFixed(1)+'%';}
function fmtWow(v){if(v===null||v===undefined)return'—';const s=v>=0?'+':'';return s+v.toFixed(1)+'pp';}
function catPill(cat){
  const cls = {'LARGE EXCESS':'p-le','EXCESS':'p-ex','NORMAL':'p-nm','DEFICIENT':'p-df','LARGE DEFICIENT':'p-ld','NO DATA':'p-nd'}[cat]||'p-nd';
  return `<span class="pill ${cls}">${cat}</span>`;
}
function devColor(v){
  if(v===null||v===undefined)return 'var(--t3)';
  if(v>=60) return 'var(--bl3)';
  if(v>=20) return 'var(--bl4)';
  if(v>=-19) return 'var(--gr4)';
  if(v>=-59) return 'var(--am4)';
  return 'var(--rd4)';
}
function wowColor(v){
  // Positive Δ = improving vs LPA (deficit narrowing / surplus widening) = blue
  // Negative Δ = worsening = red
  if(v===null||v===undefined)return 'var(--t3)';
  if(v>=5) return 'var(--bl4)';
  if(v>=-5) return 'var(--t2)';
  return 'var(--rd4)';
}

// Badges
document.getElementById('badges').innerHTML = `
  <span class="badge live">IMD Official</span>
  <span class="badge">Week ending: ${D.latestWeek||'—'}</span>
  <span class="badge">${D.subdivisions.length} subdivisions</span>
  <span class="badge">Source: imdpune.gov.in</span>`;

// Verdict
const dev = D.pan.dev;
const wow = D.pan.wow_change;
document.getElementById('verdict').innerHTML = `
  <div class="dev-block">
    <div class="dev-big" style="color:${D.verdictColor}">${fmtDev(dev)}</div>
    ${wow!==null?`<div class="dev-sub" style="color:${wowColor(wow)}">${fmtWow(wow)} w/w</div>`:''}
    <div class="dev-label">Cumulative vs LPA · Week end ${D.latestWeek||'—'}</div>
  </div>
  <h2 style="color:${D.verdictColor}">${D.verdictEmoji} ${D.verdictText}</h2>
  <p>
    <strong>IMD official pan-India cumulative departure</strong> as of week ending ${D.latestWeek||'—'}:
    <strong>${fmtDev(dev)}</strong> (${D.pan.category}).
    ${wow!==null?`Change vs prior week: <strong style="color:${wowColor(wow)}">${fmtWow(wow)}</strong>.`:''}<br>
    Subdivision distribution:
    <span class="pill p-le">LE ${D.pan.cats['LARGE EXCESS']}</span>&nbsp;
    <span class="pill p-ex">EX ${D.pan.cats['EXCESS']}</span>&nbsp;
    <span class="pill p-nm">NM ${D.pan.cats['NORMAL']}</span>&nbsp;
    <span class="pill p-df">DF ${D.pan.cats['DEFICIENT']}</span>&nbsp;
    <span class="pill p-ld">LD ${D.pan.cats['LARGE DEFICIENT']}</span>
    &nbsp;of ${D.pan.n_subs} subdivisions.
  </p>`;

// Cards
document.getElementById('cards').innerHTML = `
  <div class="card" style="border-color:${devColor(dev)};border-width:1.5px;">
    <div class="lbl">IMD Departure (Cumulative)</div>
    <div class="val" style="color:${devColor(dev)}">${fmtDev(dev)}</div>
    <div class="dt ${dev>=60?'dw':dev>=20?'dw':dev>=-19?'dg':dev>=-59?'da':'dh'}">${D.pan.category}</div></div>
  <div class="card"><div class="lbl">Week-on-Week Change</div>
    <div class="val" style="color:${wowColor(wow)}">${fmtWow(wow)}</div>
    <div class="dt dn">${wow===null?'—':(wow>=0?'improving':'worsening')}</div></div>
  <div class="card"><div class="lbl">Deficient Subdivisions</div>
    <div class="val" style="color:var(--am4)">${(D.pan.cats['DEFICIENT']+D.pan.cats['LARGE DEFICIENT'])}</div>
    <div class="dt da">of ${D.pan.n_subs}</div></div>
  <div class="card"><div class="lbl">Above-Normal Subdivisions</div>
    <div class="val" style="color:var(--bl4)">${(D.pan.cats['LARGE EXCESS']+D.pan.cats['EXCESS'])}</div>
    <div class="dt dw">of ${D.pan.n_subs}</div></div>
  <div class="card"><div class="lbl">Normal Subdivisions</div>
    <div class="val" style="color:var(--gr4)">${D.pan.cats['NORMAL']}</div>
    <div class="dt dg">of ${D.pan.n_subs}</div></div>
  <div class="card"><div class="lbl">Large Deficient Subdivisions</div>
    <div class="val" style="color:var(--rd4)">${D.pan.cats['LARGE DEFICIENT']}</div>
    <div class="dt dh">of ${D.pan.n_subs}</div></div>`;

// Regional
document.getElementById('regionGrid').innerHTML = ['NW','C','S','ENE'].map(r=>{
  const rd = D.regional[r]; if(!rd) return '';
  return `<div class="region-card">
    <div class="region-name">
      <span>${D.regionNames[r]}</span>
      ${catPill(rd.category)}
    </div>
    <div class="region-metrics">
      <div class="region-metric"><div class="lbl">Departure</div><div class="val" style="color:${devColor(rd.dev)}">${fmtDev(rd.dev)}</div></div>
      <div class="region-metric"><div class="lbl">Weekly Δ</div><div class="val" style="color:${wowColor(rd.wow_change)}">${fmtWow(rd.wow_change)}</div></div>
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

// Table
let currentSort = 'region';
document.getElementById('thead').innerHTML = `<tr>
  <th>Subdivision</th><th>Region</th>
  <th>Cumul. Departure</th><th>Weekly Δ</th><th>Category</th></tr>`;

function renderTable(){
  let s = [...D.subdivisions];
  if(currentSort === 'region')    s.sort((a,b)=> a.region.localeCompare(b.region) || ((a.dev??999) - (b.dev??999)));
  else if(currentSort === 'dev')  s.sort((a,b)=> (b.dev??-999) - (a.dev??-999));
  else if(currentSort === 'wow')  s.sort((a,b)=> (b.wowChange??-999) - (a.wowChange??-999));
  document.getElementById('subTable').innerHTML = s.map(sub=>`<tr>
    <td class="name">${sub.name}</td>
    <td style="color:var(--t3);font-size:9.5px;text-transform:uppercase;letter-spacing:0.4px;">${sub.region}</td>
    <td style="color:${devColor(sub.dev)};font-weight:700">${fmtDev(sub.dev)}</td>
    <td style="color:${wowColor(sub.wowChange)}">${fmtWow(sub.wowChange)}</td>
    <td>${catPill(sub.category)}</td>
  </tr>`).join('');
}
document.querySelectorAll('.sort-btn').forEach(btn=>{
  btn.style.cssText = 'padding:4px 10px;border-radius:3px;border:none;background:transparent;color:var(--t3);font-family:DM Sans,sans-serif;font-size:10.5px;font-weight:500;cursor:pointer;transition:all 0.2s;';
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.sort-btn').forEach(b=>{b.classList.remove('active');b.style.background='transparent';b.style.color='var(--t3)';});
    btn.classList.add('active');
    btn.style.background = 'var(--bl5)';
    btn.style.color = '#F0F9FF';
    currentSort = btn.dataset.sort;
    renderTable();
  });
});
// Style the initially active button
const initBtn = document.querySelector('.sort-btn.active');
if(initBtn){initBtn.style.background='var(--bl5)';initBtn.style.color='#F0F9FF';}
renderTable();

// Methodology
document.getElementById('method').innerHTML = `
  <strong style="color:var(--t1);font-size:12.5px">━━ DATA SOURCE ━━</strong><br><br>
  All numbers on this dashboard come directly from the <strong>India Meteorological Department (IMD)</strong>,
  specifically the Climate Research & Services Division at IMD Pune. We scrape their cumulative rainfall
  departure table published at <code>imdpune.gov.in/seasons/cumulative.html</code>. This is the same
  data that appears in IMD's weekly rainfall bulletins and is cited by news outlets, agri-analysts, and
  policy makers.<br><br>
  IMD computes each subdivision's departure from hundreds of surface stations, area-weighted, against the
  official 1971–2020 Long Period Average (LPA).<br><br>

  <strong style="color:var(--t1);font-size:12.5px">━━ UPDATE CADENCE ━━</strong><br><br>
  IMD publishes fresh data <strong>weekly on Thursdays</strong>. The latest week end shown above is
  <strong>${D.latestWeek||'—'}</strong>. Between IMD updates, the dashboard shows the last published reading unchanged
  — this is intentional; the underlying data hasn't moved.<br><br>

  <strong style="color:var(--t1);font-size:12.5px">━━ IMD DEFICIT/SURPLUS CATEGORIES ━━</strong><br><br>
  <table>
    <tr><td style="width:30%"><span class="pill p-le">LARGE EXCESS</span></td><td>≥ +60% above LPA</td></tr>
    <tr><td><span class="pill p-ex">EXCESS</span></td><td>+20% to +59%</td></tr>
    <tr><td><span class="pill p-nm">NORMAL</span></td><td>−19% to +19%</td></tr>
    <tr><td><span class="pill p-df">DEFICIENT</span></td><td>−20% to −59%</td></tr>
    <tr><td><span class="pill p-ld">LARGE DEFICIENT</span></td><td>≤ −60% below LPA</td></tr>
  </table><br>

  <strong style="color:var(--t1);font-size:12.5px">━━ AGGREGATES ━━</strong><br><br>
  The 4 broad regions (Northwest, Central, South, East & NE) and the pan-India aggregate are computed as
  <strong>simple means</strong> of subdivision-level departure %. IMD's official all-India LPA uses a proper
  area-weighted computation, so our pan-India figure may differ slightly from IMD's headline all-India number
  (typically within a few percentage points). Individual subdivision numbers match IMD verbatim.<br><br>

  <strong style="color:var(--t1);font-size:12.5px">━━ WEEK-ON-WEEK CHANGE ━━</strong><br><br>
  The "Weekly Δ" column shows how the cumulative departure changed from the prior week's reading.
  Positive Δ means the deficit is narrowing (or surplus widening) — momentum improving. Negative Δ means
  the deficit is deepening.
`;

// Footer
document.getElementById('footer').innerHTML =
  `Rain Monitor v3 (IMD-only) — Generated ${D.generatedAt} — Source: India Meteorological Department, imdpune.gov.in — Anand Consumer Research`;
</script>
</body>
</html>
'''


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("Rain Monitor v3 — India SW Monsoon (IMD-only)", file=sys.stderr)
    print("=" * 55, file=sys.stderr)

    cum_data, wkw_data = fetch_imd()
    stats, regional, pan = compute(cum_data, wkw_data)

    print(f"\nPan-India:", file=sys.stderr)
    print(f"  Cumulative dep : {pan['dev']}% ({pan['category']})", file=sys.stderr)
    print(f"  Latest week    : {pan['latest_week']}", file=sys.stderr)
    print(f"  Matched subs   : {pan['n_matched']}/{pan['n_subs']}", file=sys.stderr)
    print(f"  Cats           : {pan['cats']}", file=sys.stderr)
    print(f"  Week-on-week   : {pan['wow_change']} pp", file=sys.stderr)

    html = generate_html(stats, regional, pan)
    out_path = Path(__file__).parent / "rain_monsoon_monitor.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"\nWrote: {out_path}  ({len(html):,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
