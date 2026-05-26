"""
H₂ Elektrolyse Preisrechner – Streamlit UI
===========================================
Start:   streamlit run h2_app.py
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import textwrap
from datetime import datetime

from h2_core import (
    EEInputs, ElyInputs, ProcInputs, FleetInputs, ImportInputs,
    STRATEGY_PARAMS, SCENARIO_PARAMS, EE_MIX_PRESETS, MODEL_PARAMETERS,
    EE_DEFAULTS, compute_all, compute_ee_blend,
)

# ─────────────────────────────────────────────────────────────
# SMARD-API: Aktueller Day-Ahead-Börsenstrompreis (DE-LU)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)   # 5 min cachen
def _fetch_smard() -> dict:
    """Gibt {'price_kwh': float, 'price_mwh': float, 'ts': str, 'error': str|None} zurück."""
    try:
        idx = requests.get(
            "https://www.smard.de/app/chart_data/4169/DE-LU/index_hour.json",
            timeout=8,
        ).json()
        latest_ts = idx["timestamps"][-1]
        data = requests.get(
            f"https://www.smard.de/app/chart_data/4169/DE-LU/"
            f"4169_DE-LU_hour_{latest_ts}.json",
            timeout=8,
        ).json()
        recent = [(ts, v) for ts, v in data["series"] if v is not None]
        if not recent:
            return {"price_kwh": 0.08, "price_mwh": 80.0, "ts": "–", "error": "Keine aktuellen Werte"}
        ts_ms, price_mwh = recent[-1]
        price_kwh = max(price_mwh, 5.0) / 1000   # Negativpreise auf 5 €/MWh deckeln
        ts_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%d.%m.%Y %H:%M UTC")
        return {"price_kwh": price_kwh, "price_mwh": max(price_mwh, 5.0), "ts": ts_str, "error": None}
    except Exception as exc:
        return {"price_kwh": 0.08, "price_mwh": 80.0, "ts": "–", "error": str(exc)[:80]}


# ─────────────────────────────────────────────────────────────
# Tankstellen-Dieselpreis (benzinpreis-aktuell.de – DE-Bundesdurchschnitt)
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)   # 5 min cachen (API-Update-Intervall)
def _fetch_diesel_price() -> dict:
    """
    Aktueller DE-Dieselpreis (Bundesdurchschnitt) direkt von benzinpreis-aktuell.de.
    Kein API-Key erforderlich. Rückgabe: {'price_l', 'ts', 'error', 'source'}
    """
    try:
        data = requests.get(
            "https://www.benzinpreis-aktuell.de/api.v2.php?data=nationwide",
            timeout=8,
        ).json()
        return {
            "price_l": round(float(data["diesel"]), 3),
            "ts":      data.get("date", "–"),
            "error":   None,
            "source":  "benzinpreis-aktuell.de · DE-Bundesdurchschnitt",
        }
    except Exception as exc:
        return {
            "price_l": 1.85,
            "ts":      "–",
            "error":   str(exc)[:80],
            "source":  "Fallback (1,85 €/L)",
        }


# ─────────────────────────────────────────────────────────────
# Zusatzstrategien in STRATEGY_PARAMS registrieren
# (wird auch von compute_all / compute_proc_chain genutzt)
# ─────────────────────────────────────────────────────────────
STRATEGY_PARAMS['live'] = {
    'label': 'LIVE (SMARD)',   'icon': '🔴',   'color': '#ef4444',
    'co2': '⚡ Spot-abhängig', 'driver': 'Börsenstrompreis live',
    'priceRange': {'low': 0, 'mid': 0, 'high': 0},
    'eePrice': 0.08, 'flh': 4000,
    'flhRange': {'min': 500, 'max': 8760},
    'annH2Kg': 350000, 'storKg': 1800,
    'compKgh': 50, 'dispKgh': 50, 'liqKgh': 50, 'storDays': 0.5,
    'desc': 'Day-Ahead-Preis via SMARD-API (5 min Cache)',
}
STRATEGY_PARAMS['custom'] = {
    'label': 'Benutzerdefiniert', 'icon': '⚙️', 'color': '#a78bfa',
    'co2': '– Benutzerdefiniert', 'driver': 'Manuell',
    'priceRange': {'low': 0, 'mid': 0, 'high': 0},
    'eePrice': 0.07, 'flh': 4000,
    'flhRange': {'min': 500, 'max': 8760},
    'annH2Kg': 350000, 'storKg': 1800,
    'compKgh': 50, 'dispKgh': 50, 'liqKgh': 50, 'storDays': 0.5,
    'desc': 'Eigenen Strompreis & Volllaststunden frei wählen',
}

# =====================================================================
# Page-Konfig + Style
# =====================================================================

st.set_page_config(
    page_title="H₂ Elektrolyse Preisrechner",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ════════════════════════════════════════════════════════════
   H₂ Preisrechner – Business Theme
   Palette: Navy bg · Slate surfaces · Blue accent · Clean type
   ════════════════════════════════════════════════════════════ */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    -webkit-font-smoothing: antialiased;
}

/* ── Background ── */
.stApp { background: #0b1120; }

/* ── Metric Cards ── */
[data-testid="stMetric"] {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.07);
    border-left: 3px solid #3b82f6;
    border-radius: 6px;
    padding: 14px 18px !important;
    transition: border-left-color 0.15s;
}
[data-testid="stMetric"]:hover { border-left-color: #60a5fa; }
[data-testid="stMetricLabel"] {
    font-size: 0.68rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    color: #6b7280 !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.45rem !important;
    font-weight: 600 !important;
    color: #f1f5f9 !important;
    letter-spacing: -0.01em !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.72rem !important;
    color: #4b5563 !important;
}
.metric-blue  [data-testid="stMetricValue"] { color: #3b82f6 !important; }
.metric-green [data-testid="stMetricValue"] { color: #10b981 !important; }
.metric-gold  [data-testid="stMetricValue"] { color: #f59e0b !important; }
.metric-red   [data-testid="stMetricValue"] { color: #ef4444 !important; }

/* ── Tabs – underline style ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    gap: 0;
    padding: 0 2px;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: #6b7280;
    font-size: 0.82rem;
    font-weight: 500;
    padding: 10px 18px;
    margin-bottom: -1px;
    transition: color 0.15s, border-color 0.15s;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover {
    color: #d1d5db;
    background: rgba(255,255,255,0.02);
}
[data-testid="stTabs"] [aria-selected="true"] {
    background: transparent !important;
    border-bottom-color: #3b82f6 !important;
    color: #f1f5f9 !important;
    font-weight: 600 !important;
}
[data-testid="stTabs"] [data-baseweb="tab-panel"] {
    background: transparent;
    border: none;
    padding: 20px 0 !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #0d1117;
    border-right: 1px solid rgba(255,255,255,0.07);
}
[data-testid="stSidebar"] label {
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    color: #6b7280 !important;
    letter-spacing: 0.02em;
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 6px !important;
    margin-bottom: 6px;
}
[data-testid="stExpander"]:hover {
    border-color: rgba(255,255,255,0.12) !important;
}

/* ── DataFrames ── */
[data-testid="stDataFrame"] {
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid rgba(255,255,255,0.07) !important;
}

/* ── Alerts ── */
.stAlert { border-radius: 6px !important; border-left-width: 3px !important; }

/* ── Divider ── */
hr { border-color: rgba(255,255,255,0.07) !important; margin: 1.5rem 0 !important; }

/* ── Inputs ── */
[data-testid="stNumberInput"] > div {
    background: #1a2234 !important;
    border-radius: 5px !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
}
[data-testid="stNumberInput"] > div:focus-within {
    border-color: rgba(59,130,246,0.6) !important;
    box-shadow: 0 0 0 2px rgba(59,130,246,0.12) !important;
}

/* ── Hero Banner ── */
.h2-hero {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.08);
    border-top: 3px solid #3b82f6;
    border-radius: 8px;
    padding: 22px 28px 24px;
    margin-bottom: 20px;
}
.h2-hero h1 {
    margin: 0; font-size: 1.5rem; font-weight: 700;
    color: #f1f5f9; line-height: 1.25; letter-spacing: -0.02em;
}
.h2-hero p { margin: 5px 0 0; color: #4b5563; font-size: 0.82rem; }
.h2-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(59,130,246,0.1);
    border: 1px solid rgba(59,130,246,0.2);
    border-radius: 4px;
    padding: 3px 9px;
    font-size: 0.7rem; font-weight: 500;
    color: #93c5fd; margin-top: 10px; margin-right: 4px;
}

/* ── Section headings ── */
.section-heading {
    font-size: 0.68rem; font-weight: 600;
    letter-spacing: 0.07em; text-transform: uppercase;
    color: #4b5563; margin: 0 0 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    display: block;
}

/* ── Price tiles in hero ── */
.h2-price-strip { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start; }
.h2-price-tile {
    min-width: 88px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    padding: 10px 14px;
}
.h2-price-tile .price-label {
    font-size: 0.6rem; font-weight: 500; letter-spacing: 0.06em;
    text-transform: uppercase; color: #4b5563;
    margin-bottom: 5px; display: block;
}
.h2-price-tile .price-value {
    font-size: 1.55rem; font-weight: 700;
    color: #3b82f6; line-height: 1;
    display: block; letter-spacing: -0.02em;
}
.h2-price-tile .price-value.green { color: #10b981; }
.h2-price-tile .price-value.gold  { color: #f59e0b; }
.h2-price-tile .price-value.white { color: #e5e7eb; }
.h2-price-tile .price-unit {
    font-size: 0.65rem; color: #374151; margin-top: 3px; display: block;
}

/* ── Flowchart ── */
.flow-wrap { overflow-x: auto; margin: 0 0 10px; }
.flow-tbl {
    border-collapse: separate; border-spacing: 4px 5px;
    width: 100%; min-width: 560px;
}
.flow-tbl td { vertical-align: middle; }
.fn {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.08);
    border-top: 2px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    padding: 9px 8px; text-align: center; min-width: 88px;
    transition: border-color 0.15s;
}
.fn.sel {
    background: #1a2234;
    border-color: rgba(59,130,246,0.4);
    border-top: 2px solid #3b82f6;
}
.fn.lh2 { border-top-color: rgba(16,185,129,0.3); }
.fn.lh2.sel { border-color: rgba(16,185,129,0.4); border-top-color: #10b981; }
.fn.dim { opacity: 0.35; }
.fn .fi { font-size: 1.1rem; display: block; margin-bottom: 3px; }
.fn .fl {
    font-size: 0.62rem; font-weight: 500; color: #6b7280;
    display: block; margin-bottom: 4px; line-height: 1.2;
    text-transform: uppercase; letter-spacing: 0.04em;
}
.fn.sel .fl { color: #d1d5db; }
.fn .fc { font-size: 0.82rem; font-weight: 600; color: #3b82f6; display: block; }
.fn.lh2 .fc { color: #10b981; }
.fa {
    text-align: center; color: #374151; font-size: 1.1rem;
    padding: 0 2px; min-width: 14px; white-space: nowrap;
}
.fb {
    text-align: center; color: #374151; font-size: 1.0rem;
    padding: 0 2px; white-space: pre; line-height: 1.4; vertical-align: middle;
}
.lane-tag {
    font-size: 0.58rem; font-weight: 600; letter-spacing: 0.07em;
    text-transform: uppercase; padding: 2px 6px;
    border-radius: 3px; white-space: nowrap; display: inline-block;
}

/* ── Sidebar stat rows ── */
.sb-stat {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.05);
    font-size: 0.78rem;
}
.sb-stat:last-child { border-bottom: none; }
.sb-stat .sk { color: #4b5563; font-weight: 400; }
.sb-stat .sv { color: #e5e7eb; font-weight: 600; }
.sb-stat .sv.accent { color: #3b82f6; }
</style>
""", unsafe_allow_html=True)


# =====================================================================
# Sidebar: Strategie × Szenario
# =====================================================================

st.sidebar.markdown("""
<div style="
    background: #111827;
    border: 1px solid rgba(255,255,255,0.07);
    border-top: 3px solid #3b82f6;
    border-radius: 6px;
    padding: 16px 18px;
    margin-bottom: 12px;
">
    <div style="font-size:1.3rem; font-weight:700; color:#f1f5f9; line-height:1.2;">
        ⚡ H₂-Preisrechner
    </div>
    <div style="font-size:0.72rem; color:#4b5563; margin-top:4px;">
        v1.0 &nbsp;·&nbsp; Python-Port
    </div>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown('<p style="font-size:0.7rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#4b5563;margin-bottom:4px;">STRATEGIE</p>', unsafe_allow_html=True)
strat_options = {k: f"{v['icon']} {v['label']}" for k, v in STRATEGY_PARAMS.items()}
active_strategy = st.sidebar.selectbox(
    "Strategie", options=list(strat_options.keys()),
    format_func=lambda k: strat_options[k], index=1, key="sb_strategy",
    label_visibility="collapsed",
)
st.sidebar.caption(STRATEGY_PARAMS[active_strategy]['desc'])

st.sidebar.markdown('<p style="font-size:0.7rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#4b5563;margin:12px 0 4px;">SZENARIO</p>', unsafe_allow_html=True)
active_scenario = st.sidebar.selectbox(
    "Szenario", options=list(SCENARIO_PARAMS.keys()),
    format_func=lambda k: SCENARIO_PARAMS[k]['label'], index=1, key="sb_scenario",
    label_visibility="collapsed",
)

# Szenario-Defaults laden
scn  = SCENARIO_PARAMS[active_scenario]
strat = STRATEGY_PARAMS[active_strategy]

# ── Szenario-Wechsel: Widget-Werte zurücksetzen ──────────────
if st.session_state.get('_last_scenario') != active_scenario:
    updates = {
        'ely_capex': int(scn['elyCAPEX']),   'ely_opex':  float(scn['elyOPEX']),
        'ely_life':  int(scn['elyLife']),     'ely_wacc':  float(scn['elyWACC']),
        'comp_eta':  scn['compEta'] / 100,    'comp_capex': int(scn['compCAPEX']),
        'comp_opex': float(scn['compOPEX']),  'comp_life':  int(scn['compLife']),
        'stor_capex': int(scn['storCAPEX']),  'stor_opex':  float(scn['storOPEX']),
        'stor_life':  int(scn['storLife']),   'disp_util':  float(scn['dispUtil']),
        'disp_opex':  float(scn['dispOPEX']), 'disp_life':  int(scn['dispLife']),
    }
    for k, v in updates.items():
        st.session_state[k] = v
    st.session_state['_last_scenario'] = active_scenario

# ── Strategie-Wechsel: Slider-Defaults zurücksetzen ──────────
if st.session_state.get('_last_strategy') != active_strategy:
    st.session_state['ely_flh'] = int(strat['flh'])
    # Preis- und VLH-Slider auf Strategie-Defaults zurücksetzen
    st.session_state[f"sb_price_{active_strategy}"] = float(strat['priceRange']['mid'])
    st.session_state[f"sb_flh_{active_strategy}"]   = int(strat['flh'])
    st.session_state['_last_strategy'] = active_strategy

# ── LIVE-Strategie: SMARD-Preis laden ────────────────────────
_smard          = None
_live_price_kwh = strat['eePrice']
_live_flh       = int(strat['flh'])

if active_strategy == 'live':
    st.sidebar.markdown("---")
    _smard = _fetch_smard()
    if _smard['error']:
        st.sidebar.warning(f"⚠️ SMARD: {_smard['error']} – Fallback 80 €/MWh")
    else:
        st.sidebar.markdown(
            '<div style="background:#111827;border:1px solid rgba(255,255,255,0.07);'
            'border-left:3px solid #ef4444;border-radius:6px;padding:12px 14px;margin-bottom:8px;">'
            '<div style="font-size:0.62rem;font-weight:600;letter-spacing:0.07em;'
            'text-transform:uppercase;color:#6b7280;margin-bottom:6px;">🔴 SMARD Day-Ahead live</div>'
            f'<div style="font-size:1.6rem;font-weight:700;color:#f87171;line-height:1;">'
            f'{_smard["price_mwh"]:.1f}'
            '<span style="font-size:0.85rem;font-weight:400;color:#6b7280;"> €/MWh</span></div>'
            f'<div style="font-size:0.67rem;color:#4b5563;margin-top:5px;">'
            f'= {_smard["price_kwh"]*100:.2f} ct/kWh · {_smard["ts"]}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    _live_price_kwh = _smard['price_kwh']
    # FLH-Slider für LIVE
    st.sidebar.markdown('<p style="font-size:0.7rem;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#4b5563;margin:8px 0 2px;">VOLLLASTSTUNDEN</p>', unsafe_allow_html=True)
    _live_flh = st.sidebar.slider(
        "FLH live", min_value=500, max_value=8760,
        value=st.session_state.get('live_flh_val', 4000), step=100,
        key="live_flh_slider", label_visibility="collapsed",
    )
    st.session_state['live_flh_val'] = _live_flh
    st.session_state['ely_flh'] = _live_flh
    # SMARD-Preis live im strat-Dict überschreiben
    STRATEGY_PARAMS['live']['eePrice'] = _live_price_kwh
    STRATEGY_PARAMS['live']['flh']     = _live_flh

# ── BENUTZERDEFINIERT: eigener Preis ─────────────────────────
elif active_strategy == 'custom':
    st.sidebar.markdown("---")
    st.sidebar.markdown('<p style="font-size:0.7rem;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#4b5563;margin-bottom:4px;">BENUTZERDEFINIERTER STROMPREIS</p>', unsafe_allow_html=True)
    _custom_ct = st.sidebar.number_input(
        "Strompreis [ct/kWh]",
        min_value=0.5, max_value=80.0,
        value=st.session_state.get('custom_price_ct', 7.0),
        step=0.5, format="%.1f",
        key="custom_price_input", label_visibility="collapsed",
    )
    st.session_state['custom_price_ct'] = _custom_ct
    st.sidebar.markdown(
        f'<div style="background:#111827;border:1px solid rgba(255,255,255,0.07);'
        f'border-left:3px solid #3b82f6;border-radius:6px;padding:8px 14px;margin-bottom:8px;">'
        f'<span style="font-size:1.4rem;font-weight:700;color:#3b82f6;">{_custom_ct:.1f} ct/kWh</span>'
        f'<span style="font-size:0.72rem;color:#4b5563;display:block;">{_custom_ct*10:.1f} €/MWh</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown('<p style="font-size:0.7rem;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#4b5563;margin:8px 0 2px;">VOLLLASTSTUNDEN</p>', unsafe_allow_html=True)
    _live_flh = st.sidebar.slider(
        "FLH custom", min_value=500, max_value=8760,
        value=st.session_state.get('custom_flh_val', 4000), step=100,
        key="custom_flh_slider", label_visibility="collapsed",
    )
    st.session_state['custom_flh_val'] = _live_flh
    st.session_state['ely_flh'] = _live_flh
    _live_price_kwh = _custom_ct / 100
    STRATEGY_PARAMS['custom']['eePrice'] = _live_price_kwh
    STRATEGY_PARAMS['custom']['flh']     = _live_flh

else:
    # ── Standard-Strategien: Preis & VLH anpassbar ───────────
    st.sidebar.markdown("---")
    _pr = strat['priceRange']
    _fr = strat['flhRange']
    _price_key = f"sb_price_{active_strategy}"
    _flh_key   = f"sb_flh_{active_strategy}"

    st.sidebar.markdown(
        '<p style="font-size:0.7rem;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;color:#4b5563;margin-bottom:2px;">STROMPREIS</p>',
        unsafe_allow_html=True,
    )
    # Low/Mid/High-Marker als Kontext
    st.sidebar.markdown(
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:0.62rem;color:#4b5563;margin-bottom:2px;">'
        f'<span>{_pr["low"]:.0f}</span><span style="color:#6b7280;">Ø {_pr["mid"]:.1f} €/MWh</span>'
        f'<span>{_pr["high"]:.0f}</span></div>',
        unsafe_allow_html=True,
    )
    _strat_price_mwh = st.sidebar.slider(
        "Strompreis",
        min_value=float(_pr['low']),
        max_value=float(_pr['high']),
        value=float(st.session_state.get(_price_key, _pr['mid'])),
        step=0.5,
        format="%.1f €/MWh",
        key=_price_key,
        label_visibility="collapsed",
    )
    _live_price_kwh = _strat_price_mwh / 1000
    STRATEGY_PARAMS[active_strategy]['eePrice'] = _live_price_kwh

    st.sidebar.markdown(
        '<p style="font-size:0.7rem;font-weight:700;letter-spacing:0.1em;'
        'text-transform:uppercase;color:#4b5563;margin:8px 0 2px;">VOLLLASTSTUNDEN</p>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:0.62rem;color:#4b5563;margin-bottom:2px;">'
        f'<span>{_fr["min"]:,}</span>'
        f'<span style="color:#6b7280;">Default {strat["flh"]:,} h/a</span>'
        f'<span>{_fr["max"]:,}</span></div>',
        unsafe_allow_html=True,
    )
    _live_flh = st.sidebar.slider(
        "VLH",
        min_value=int(_fr['min']),
        max_value=int(_fr['max']),
        value=int(st.session_state.get(_flh_key, strat['flh'])),
        step=100,
        key=_flh_key,
        label_visibility="collapsed",
    )
    st.session_state['ely_flh'] = _live_flh
    STRATEGY_PARAMS[active_strategy]['flh'] = _live_flh

# ── Netzstrompreis (Hybrid-Chart: Weiterbetrieb nach VLH) ────
st.sidebar.markdown(
    '<p style="font-size:0.7rem;font-weight:700;letter-spacing:0.1em;'
    'text-transform:uppercase;color:#4b5563;margin:8px 0 2px;">NETZSTROMPREIS</p>',
    unsafe_allow_html=True,
)
_sb_strom_ct = st.sidebar.number_input(
    "Netzstrom [ct/kWh]",
    min_value=0.5, max_value=80.0,
    value=15.0,
    step=0.5, format="%.1f",
    key="sb_strom_input",
)
_sidebar_strom_eur_kwh = _sb_strom_ct / 100

# ── Strategie-Infokarte ───────────────────────────────────────
st.sidebar.markdown("---")
_price_display = f"{_live_price_kwh*1000:.1f} €/MWh"
_flh_display   = _live_flh
_accent = {'live': '#ef4444', 'custom': '#3b82f6'}.get(active_strategy, strat['color'])
st.sidebar.markdown(f"""
<div style="background:#111827;border:1px solid rgba(255,255,255,0.07);
            border-radius:6px;padding:14px 16px;font-size:0.82rem;line-height:1.8;">
    <div style="font-weight:600;color:#f1f5f9;margin-bottom:8px;
                font-size:0.85rem;letter-spacing:-0.01em;">
        {strat['icon']} {strat['label']}
    </div>
    <div class="sb-stat">
        <span class="sk">🌿 CO₂</span>
        <span class="sv">{strat['co2']}</span>
    </div>
    <div class="sb-stat">
        <span class="sk">⚙️ Treiber</span>
        <span class="sv">{strat['driver']}</span>
    </div>
    <div class="sb-stat">
        <span class="sk">💡 Strom</span>
        <span class="sv accent" style="color:{_accent};">{_price_display}</span>
    </div>
    <div class="sb-stat">
        <span class="sk">⏱ FLH</span>
        <span class="sv accent" style="color:{_accent};">{_flh_display:,} h/a</span>
    </div>
</div>
""", unsafe_allow_html=True)


# =====================================================================
# Tabs
# =====================================================================

_tab_labels = [
    "📊 Übersicht", "⚡ Elektrolyseur", "🔧 Prozesskette",
    *( ["☀️ Strommix"] if active_strategy == 'custom' else [] ),
    "🚛 Flotte/TCO", "🚢 Import-Vergleich", "📈 Sensitivität",
]
_tabs = st.tabs(_tab_labels)
_ee_offset = 1 if active_strategy == 'custom' else 0
tab_overview = _tabs[0]
tab_ely      = _tabs[1]
tab_proc     = _tabs[2]
tab_ee       = _tabs[3]       if active_strategy == 'custom' else None
tab_fleet    = _tabs[3 + _ee_offset]
tab_import   = _tabs[4 + _ee_offset]
tab_sens     = _tabs[5 + _ee_offset]


# =====================================================================
# Inputs sammeln (Sidebar + Tab-spezifisch)
# =====================================================================

# ── EE-Inputs: Defaults (auch wenn Strommix-Tab ausgeblendet) ─────────────────────
ee_mode    = st.session_state.get("ee_mode", "Preset")
ee_preset  = st.session_state.get("ee_preset", "70% PV + 30% Wind Onshore")
grid_price = st.session_state.get("ee_grid_price", 0.15)

if tab_ee is not None:
    with tab_ee:
        st.subheader("Strommix (LCOE)")
        col1, col2 = st.columns([1, 2])
        with col1:
            ee_mode = st.radio("Modus", ["Preset", "Custom"], horizontal=True, key="ee_mode")
            ee_preset = st.selectbox("Preset", list(EE_MIX_PRESETS.keys()), index=2, key="ee_preset")
            grid_price = st.slider("Netzstrompreis [€/kWh]", 0.05, 0.30, 0.15, 0.005, key="ee_grid_price")

            if ee_mode == "Custom":
                st.markdown('<p class="section-heading" style="margin-top:12px;">Technologieanteile</p>',
                            unsafe_allow_html=True)
                _custom_sources = {}
                _tech_share_defaults = {'Photovoltaik': 50, 'Wind Onshore': 30, 'Wind Offshore': 15, 'Wasserkraft': 5}
                for _tech, _def in EE_DEFAULTS.items():
                    _en = st.checkbox(_tech, value=False, key=f"ee_cust_en_{_tech}")
                    if _en:
                        _sh = st.slider(f"Anteil {_tech} [%]", 0, 100,
                                        _tech_share_defaults.get(_tech, 25), 5, key=f"ee_cust_sh_{_tech}")
                        _cx = st.number_input(f"CAPEX {_tech} [€/kW]", 200, 6000,
                                              int(_def['capex']), 50, key=f"ee_cust_cx_{_tech}")
                        _fh = st.number_input(f"FLH {_tech} [h/a]", 200, 8000,
                                              int(_def['flh']), 50, key=f"ee_cust_flh_{_tech}")
                        _custom_sources[_tech] = {'share': _sh, 'capex': _cx, 'flh': _fh,
                                                  'opex': _def['opex'], 'life': _def['life']}

        # Vorschau-Chart: LCOE je Technologie (Referenzwerte)
        with col2:
            st.markdown('<p class="section-heading">LCOE-Referenz je Technologie (WACC 6 %)</p>',
                        unsafe_allow_html=True)
            _ref_rows = []
            _ref_wacc = 0.06
            for _tname, _td in EE_DEFAULTS.items():
                _n = _td['life']
                _crf = (_ref_wacc * (1 + _ref_wacc)**_n) / ((1 + _ref_wacc)**_n - 1)
                _lcoe_kwh = (_td['capex'] * (_crf + _td['opex'] / 100)) / _td['flh']
                _ref_rows.append({'Technologie': _tname,
                                   'LCOE ct/kWh': round(_lcoe_kwh * 100, 2),
                                   'FLH h/a': _td['flh'],
                                   'CAPEX €/kW': _td['capex']})
            _ref_df = pd.DataFrame(_ref_rows)
            _active_techs = [t for t, _ in EE_MIX_PRESETS.get(ee_preset, [])]
            _bar_c = ['#3b82f6' if r in _active_techs else '#374151'
                      for r in _ref_df['Technologie']]
            _fig_ref = go.Figure(go.Bar(
                x=_ref_df['Technologie'], y=_ref_df['LCOE ct/kWh'],
                marker_color=_bar_c, marker_line_width=0,
                text=_ref_df['LCOE ct/kWh'].apply(lambda v: f"{v:.2f}"),
                textposition='outside', textfont=dict(color='#cbd5e1', size=11),
                hovertemplate='<b>%{x}</b><br>LCOE: %{y:.2f} ct/kWh<extra></extra>',
            ))
            _fig_ref.update_layout(
                height=240, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                xaxis_title='', yaxis_title='LCOE [ct/kWh]',
                font=dict(family='Inter', color='#94a3b8'),
                margin=dict(l=0, r=10, t=10, b=0),
                xaxis=dict(gridcolor='rgba(255,255,255,0.04)'),
                yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
                hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
            )
            st.plotly_chart(_fig_ref, use_container_width=True)
            st.caption("Blau = im gewählten Preset aktiv · Werte bei WACC 6 %, ohne Netzpreis")

if ee_mode == 'Custom' and 'tab_ee' in dir() and tab_ee is not None:
    # Baue EESource-Objekte aus den UI-Widgets
    _ee_sources_custom = [
        EESource(on=True, tech=_t, share=float(_v['share']),
                 capex=float(_v['capex']), opex=float(_v['opex']),
                 flh=float(_v['flh']), life=float(_v['life']),
                 wacc=6.0, dep=False)
        for _t, _v in _custom_sources.items()
    ] if '_custom_sources' in dir() and _custom_sources else []
    ee = EEInputs(mode='Custom', preset=ee_preset, grid_price_eur_kwh=grid_price,
                  sources=_ee_sources_custom if _ee_sources_custom else EEInputs().sources)
else:
    ee = EEInputs(mode=ee_mode, preset=ee_preset, grid_price_eur_kwh=grid_price)


with tab_ely:
    _ely_left, _ely_right = st.columns([3, 2])

    with _ely_left:
        st.markdown('<p class="section-heading">Betrieb & Technologie</p>', unsafe_allow_html=True)
        _ea, _eb_ = st.columns(2)
        with _ea:
            ely_power = st.number_input("⚡ Leistung [MW]", 0.1, 500.0, 5.0, 0.5, key="ely_power")
            ely_flh   = st.number_input("⏱ Volllaststunden [h/a]", 500, 8760,
                                         int(strat['flh']), key="ely_flh")
            ely_avail = st.number_input("✅ Verfügbarkeit [%]", 50.0, 100.0, 95.0, 1.0,
                                         key="ely_avail")
            ely_aux   = st.number_input("🔌 Aux [%]", 0.0, 15.0, 3.0, 0.5,
                                         key="ely_aux")
        with _eb_:
            use_eta = st.checkbox("η statt Verbrauch", value=False, key="ely_use_eta")
            if use_eta:
                ely_eta  = st.slider("η LHV [%]", 50.0, 85.0, float(scn['elyEta']),
                                     0.5, key="ely_eta_slider")
                ely_spec = 33.33 / (ely_eta / 100)
                st.markdown(
                    f'<div style="background:#1a2234;border:1px solid rgba(59,130,246,0.2);'
                    f'border-radius:5px;padding:8px 12px;margin-top:4px;">'
                    f'<span style="font-size:0.7rem;color:#4b5563;">Spez. Verbrauch</span><br>'
                    f'<span style="font-size:1.2rem;font-weight:600;color:#3b82f6;">'
                    f'{ely_spec:.1f} kWh/kg</span></div>',
                    unsafe_allow_html=True,
                )
            else:
                ely_spec = st.number_input("🔋 Spez. Verbrauch [kWh/kg]", 40.0, 70.0,
                                            57.0, 0.5, key="ely_spec")
                ely_eta  = 58.0
                _eta_disp = 33.33 / ely_spec * 100
                st.caption(f"⇒ η LHV ≈ {_eta_disp:.1f} %")

    with _ely_right:
        with st.expander("💰 Investition & Finanzierung", expanded=True):
            ely_capex = st.number_input("CAPEX [€/kW]", 100, 3000,
                                         int(scn['elyCAPEX']), key="ely_capex")
            ely_opex  = st.number_input("OPEX [%/a]", 0.5, 10.0,
                                         float(scn['elyOPEX']), 0.5, key="ely_opex")
            ely_life  = st.number_input("Lebensdauer [a]", 5, 40,
                                         int(scn['elyLife']), key="ely_life")
            ely_wacc  = st.number_input("WACC [%]", 1.0, 15.0,
                                         float(scn['elyWACC']), 0.5, key="ely_wacc")
            # Live CAPEX-Annuität Vorschau
            _n = max(ely_life, 1)
            _w = ely_wacc / 100
            _crf = (_w * (1 + _w)**_n) / ((1 + _w)**_n - 1) if _w > 0 else 1/_n
            _ann_capex = ely_capex * _crf
            st.markdown(
                f'<div style="background:#0d1117;border:1px solid rgba(255,255,255,0.06);'
                f'border-radius:5px;padding:8px 12px;margin-top:4px;">'
                f'<span style="font-size:0.62rem;color:#4b5563;text-transform:uppercase;'
                f'letter-spacing:0.06em;">Kapitalkosten (CRF)</span><br>'
                f'<span style="font-size:1.05rem;font-weight:600;color:#f1f5f9;">'
                f'{_ann_capex:.1f} €/kW·a</span>'
                f'<span style="font-size:0.7rem;color:#4b5563;"> · CRF {_crf*100:.2f} %</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        with st.expander("🔄 Stack-Ersatz"):
            stack_replace = st.checkbox("Stack-Ersatz berücksichtigen",
                                         value=True, key="stack_replace")
            if stack_replace:
                stack_h   = st.number_input("Stack-Lebensdauer [h]", 20_000, 200_000,
                                             60_000, 5_000, key="stack_h")
                stack_pct = st.number_input("Ersatzkosten [% CAPEX]", 0.0, 60.0,
                                             35.0, 5.0, key="stack_pct")
            else:
                stack_h, stack_pct = 60_000, 35.0

    ely = ElyInputs(
        power_mw=ely_power, capex_eur_kw=ely_capex, opex_pct=ely_opex,
        avail_pct=ely_avail, aux_pct=ely_aux, flh=ely_flh, life_a=ely_life,
        wacc_pct=ely_wacc, stack_life_h=stack_h, stack_replace=stack_replace,
        stack_pct=stack_pct, specific_kwh_kg=ely_spec, use_efficiency=use_eta,
        eta_lhv_pct=ely_eta,
    )

with tab_proc:
    # ── Step-Selektor (Flowchart-Navigation) ─────────────────
    if 'proc_step' not in st.session_state:
        st.session_state.proc_step = 'comp'
    _sel_inp = st.session_state.proc_step
    _lh2_on  = st.session_state.get('liq_on', False)

    def _fn(key, icon, label, cost='', is_lh2=False):
        sel_cls = ' sel' if (_sel_inp == key) else ''
        lh2_cls = ' lh2' if is_lh2 else ''
        dim_cls = ' dim' if (is_lh2 and not _lh2_on) else ''
        cost_html = f'<span class="fc">{cost}</span>' if cost else ''
        return (f'<td class="fn{sel_cls}{lh2_cls}{dim_cls}">'
                f'<span class="fi">{icon}</span>'
                f'<span class="fl">{label}</span>'
                f'{cost_html}</td>')

    _shared_style = ('background:#111827;'
                     'border:1px solid rgba(255,255,255,0.08);border-top:2px solid #3b82f6;border-radius:6px;'
                     'text-align:center;padding:10px 10px;min-width:80px;vertical-align:middle;')

    st.markdown(textwrap.dedent(f"""
    <div class="flow-wrap">
    <table class="flow-tbl">
      <tr>
        <td rowspan="2" style="{_shared_style}">
          <span style="font-size:1.25rem;">⚡</span><br>
          <span style="font-size:0.68rem;font-weight:600;color:#3b82f6;">ELY</span>
        </td>
        <td rowspan="2" class="fa">→</td>
        <td rowspan="2" style="{_shared_style}">
          <span style="font-size:1.25rem;">📦</span><br>
          <span style="font-size:0.68rem;font-weight:600;color:#6b7280;">GH₂-Puffer</span>
        </td>
        <td rowspan="2" class="fb">─┬<br>&nbsp;└</td>
        {_fn('comp','🔧','Kompressor')}
        <td class="fa">→</td>
        {_fn('stor','📦','CGH₂-Speicher')}
        <td class="fa">→</td>
        {_fn('disp','⛽','Vertankung')}
        <td style="padding-left:6px;">
          <span class="lane-tag" style="background:rgba(59,130,246,0.1);color:#3b82f6;border:1px solid rgba(59,130,246,0.2);">CGH₂</span>
        </td>
      </tr>
      <tr>
        {_fn('lh2','❄️','Verflüssigung',is_lh2=True)}
        <td class="fa{'dim' if not _lh2_on else ''}">→</td>
        {_fn('lh2','🧊','LH₂-Speicher',is_lh2=True)}
        <td class="fa{'dim' if not _lh2_on else ''}">→</td>
        {_fn('lh2','⛽','LH₂-Vertankung',is_lh2=True)}
        <td style="padding-left:6px;">
          <span class="lane-tag" style="{'opacity:0.4;' if not _lh2_on else ''}background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.2);">LH₂</span>
        </td>
      </tr>
    </table>
    </div>
    """), unsafe_allow_html=True)

    # ── Klick-Buttons unter dem Flowchart ─────────────────
    _btn_steps = [('comp','🔧 Kompressor'), ('stor','📦 Speicher'),
                  ('disp','⛽ Vertankung'), ('lh2','❄️ LH₂-Pfad')]
    _bcols = st.columns(len(_btn_steps))
    for _bi, (_bk, _bl) in enumerate(_btn_steps):
        _is_active = (_sel_inp == _bk)
        if _bcols[_bi].button(
            f"{'✓ ' if _is_active else ''}{_bl}",
            key=f"flow_btn_{_bk}",
            use_container_width=True,
            type="primary" if _is_active else "secondary",
        ):
            st.session_state.proc_step = _bk
            st.rerun()

    st.markdown("---")

    _sel = st.session_state.proc_step

    with st.expander("🔧 Kompression", expanded=(_sel == 'comp')):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            p1 = st.number_input("Eingangsdruck [bar]", 1.0, 100.0, 30.0, key="comp_p1")
            p2 = st.number_input("Ausgangsdruck [bar]", 50.0, 1000.0, 350.0, key="comp_p2")
        with c2:
            comp_eta = st.slider("Wirkungsgrad", 0.50, 0.95, scn['compEta'] / 100, 0.01, key="comp_eta")
            comp_capex = st.number_input("CAPEX [€/(kg/h)]", 5_000, 100_000, int(scn['compCAPEX']), key="comp_capex")
        with c3:
            comp_opex = st.number_input("OPEX [%/a]", 0.5, 10.0, float(scn['compOPEX']), 0.5, key="comp_opex")
            comp_life = st.number_input("Lebensdauer [a]", 5, 30, int(scn['compLife']), key="comp_life")
            comp_loss = st.number_input("Verlust [%]", 0.0, 5.0, 1.0, 0.1, key="comp_loss")
        with c4:
            comp_util = st.number_input(
                "Ziel-Auslastung Kompressor [%]", 30.0, 95.0, 70.0, 5.0,
                key="comp_util",
                help=(
                    "Kompressor läuft entkoppelt vom ELY über den GH₂-Puffer @30 bar.\n\n"
                    "Auslegung: ann_H₂ ÷ (8.760 h × Auslastung)\n"
                    "70 % → ca. 6.130 h/a Betrieb"
                ),
            )

    with st.expander("📦 CGH₂-Speicher", expanded=(_sel == 'stor')):
        c1, c2, c3 = st.columns(3)
        with c1:
            stor_days = st.number_input("Speichertage", 0.1, 30.0, 2.0, 0.1, key="stor_days")
            stor_capex = st.number_input("CAPEX [€/kg]", 100, 3000, int(scn['storCAPEX']), key="stor_capex")
        with c2:
            stor_opex = st.number_input("OPEX [%/a]", 0.5, 5.0, float(scn['storOPEX']), 0.5, key="stor_opex")
            stor_life = st.number_input("Lebensdauer [a]", 10, 40, int(scn['storLife']), key="stor_life")
        with c3:
            gh2_buf_days = st.number_input("GH₂-Puffer [Tage]", 0.0, 10.0, 1.0, 0.5, key="gh2_buf_days")
            gh2_buf_capex = st.number_input("GH₂-Puffer CAPEX [€/kg]", 100, 2000, 300, key="gh2_buf_capex")

    with st.expander("⛽ Vertankung", expanded=(_sel == 'disp')):
        c1, c2, c3 = st.columns(3)
        with c1:
            disp_util = st.number_input("Auslastung [%]", 20.0, 100.0, float(scn['dispUtil']), 5.0, key="disp_util")
            disp_life = st.number_input("Lebensdauer [a]", 5, 25, int(scn['dispLife']), key="disp_life")
        with c2:
            disp_opex = st.number_input("OPEX [%/a]", 0.5, 5.0, float(scn['dispOPEX']), 0.5, key="disp_opex")
            disp_loss = st.number_input("Verlust [%]", 0.0, 10.0, 2.0, 0.5, key="disp_loss")
        with c3:
            st.caption(f"Kapazität aus Strategie: **{strat['dispKgh']} kg/h**")
            st.caption("CAPEX wird automatisch skaliert (Power-Law).")

    with st.expander("❄️ LH₂-Pfad (Verflüssigung + LH₂-Speicher/Vertankung)", expanded=(_sel == 'lh2')):
        liq_on = st.checkbox("LH₂-Pfad aktivieren", value=False, key="liq_on")
        if liq_on:
            c1, c2, c3 = st.columns(3)
            with c1:
                liq_sec = st.number_input("Spez. Strom Verflüssigung [kWh/kg]", 5.0, 20.0, float(scn['liqSEC']), key="liq_sec")
                liq_capex = st.number_input("CAPEX Verflüssigung [€/(kg/h)]", 10_000, 500_000, int(scn['liqCAPEX']), 5_000, key="liq_capex")
            with c2:
                liq_opex = st.number_input("OPEX Verflüssigung [%/a]", 1.0, 10.0, float(scn['liqOPEX']), key="liq_opex")
                liq_life = st.number_input("Lebensdauer Verflüssigung [a]", 10, 30, int(scn['liqLife']), key="liq_life")
            with c3:
                liq_util = st.number_input(
                    "Ziel-Auslastung Verflüssiger [%]", 30.0, 95.0, 70.0, 5.0,
                    key="liq_util",
                    help=(
                        "Der Verflüssiger wird kleiner ausgelegt als der ELY-Peak, "
                        "weil er über den GH₂-Puffer kontinuierlicher läuft.\n\n"
                        "Auslegung: ann_H₂ ÷ (8.760 h × Auslastung)\n"
                        "70 % → ca. 6.130 h/a Betrieb"
                    ),
                )
                lh2_days = st.number_input("LH₂-Speichertage", 0.5, 15.0, float(scn['lh2Days']), key="lh2_days")
                lh2_boiloff = st.number_input("Boil-off [%/Tag]", 0.01, 1.0, float(scn['lh2Boiloff']), 0.05, key="lh2_boiloff")
        else:
            liq_sec = 13.0
            liq_capex = scn.get('liqCAPEX', 130_000)
            liq_opex = scn.get('liqOPEX', 4.0)
            liq_life = scn.get('liqLife', 22)
            liq_util = 70.0
            lh2_days = scn.get('lh2Days', 0.5)
            lh2_boiloff = scn.get('lh2Boiloff', 0.20)

    proc = ProcInputs(
        p1_bar=p1, p2_bar=p2, comp_eta=comp_eta,
        comp_capex_eur_kgh=comp_capex, comp_opex_pct=comp_opex, comp_life_a=comp_life,
        comp_loss_pct=comp_loss, comp_util_target_pct=comp_util,
        stor_days=stor_days, stor_capex_eur_kg=stor_capex, stor_opex_pct=stor_opex,
        stor_life_a=stor_life,
        gh2_buf_days=gh2_buf_days, gh2_buf_capex_eur_kg=gh2_buf_capex,
        gh2_buf_opex_pct=float(scn.get('gh2BufOPEX', 2.0)),
        gh2_buf_life_a=float(scn.get('gh2BufLife', 20)),
        disp_util_pct=disp_util, disp_opex_pct=disp_opex, disp_life_a=disp_life,
        disp_loss_pct=disp_loss,
        liq_on=liq_on, liq_sec_kwh_kg=liq_sec, liq_capex_eur_kgh=liq_capex,
        liq_opex_pct=liq_opex, liq_life_a=liq_life, liq_util_target_pct=liq_util,
        lh2_stor_days=lh2_days, lh2_boiloff_pct_per_day=lh2_boiloff,
        lh2_stor_capex_eur_kg=scn.get('lh2StorCAPEX', 350),
        lh2_stor_opex_pct=float(scn.get('lh2StorOPEX', 2.0)),
        lh2_stor_life_a=scn.get('lh2StorLife', 27),
        lh2_disp_capex_keur=scn.get('lh2DispCAPEX', 560),
        lh2_disp_util_pct=scn.get('lh2DispUtil', 80),
        lh2_disp_opex_pct=scn.get('lh2DispOPEX', 2.5),
        lh2_disp_life_a=scn.get('lh2DispLife', 15),
    )

with tab_fleet:
    st.subheader("Flotte / TCO")
    _diesel_api  = _fetch_diesel_price()
    _diesel_netto = round(_diesel_api["price_l"] / 1.19, 3)   # Brutto → Netto (excl. 19% MwSt)
    _diesel_default = float(st.session_state.get("fleet_diesel", _diesel_netto))
    c1, c2, c3 = st.columns(3)
    with c1:
        ann_km = st.number_input("Jahreskilometer", 10_000, 500_000, 120_000, 5_000, key="fleet_km")
        fleet_life = st.number_input("Haltedauer [a]", 1, 15, 5, key="fleet_life")
    with c2:
        fleet_wacc = st.number_input("WACC Flotte [%]", 1.0, 15.0, 6.0, 0.5, key="fleet_wacc")
        diesel_price = st.number_input("Dieselpreis netto [€/l]", 0.50, 3.00, _diesel_default, 0.01, key="fleet_diesel")
        if _diesel_api["error"] is None:
            st.caption(f"🔴 Live · {_diesel_api['price_l']:.3f} €/l brutto → **{_diesel_netto:.3f} €/l netto** (÷1,19 MwSt) · {_diesel_api['ts']}")
        else:
            st.caption(f"⚠️ Fallback-Preis (API: {_diesel_api['error']})")
    with c3:
        toll_share = st.slider("Mautanteil [%]", 0, 100, 80, 5, key="fleet_toll") / 100
        ice_ref = st.number_input("ICE Referenzpreis [€]", 50_000, 300_000, 150_000, 5_000, key="fleet_ice_ref")

    fleet = FleetInputs(
        annual_km=ann_km, life_a=fleet_life, wacc_pct=fleet_wacc,
        toll_share=toll_share, diesel_price_eur_l=diesel_price,
        ice_ref_price_eur=ice_ref,
    )

with tab_import:
    st.subheader("Import-Vergleich (NH₃-Route)")
    c1, c2, c3 = st.columns(3)
    with c1:
        dist_km = st.number_input("Lkw-Distanz Hafen→Verbraucher [km]", 0, 3000, 450, 50, key="imp_dist")
    with c2:
        trans_ct = st.number_input("Lkw-Transport Hafen→Verbraucher [ct/kg·km]", 0.0, 5.0, 0.667, 0.05,
                                   key="imp_trans",
                                   help="Tube-Trailer-Transport: ca. 0.3–1.0 ct/kg·km")
    with c3:
        price_a = st.number_input("Terminalpreis A – Markt [€/kg]", 1.0, 15.0, 4.90, 0.10,
                                  key="imp_price_a",
                                  help="H₂-Preis frei Importterminal (inkl. Produktion, Seefracht, NH₃-Synthese & Cracking)")
        price_b = st.number_input("Terminalpreis B – staatl. abges. [€/kg]", 1.0, 15.0, 3.80, 0.10,
                                  key="imp_price_b",
                                  help="Subventionierter / staatlich abgesicherter Importpreis (z. B. H2Global-Mechanismus)")
    imp = ImportInputs(
        distance_km=dist_km, transport_ct_per_kg_km=trans_ct,
        price_a_eur_kg=price_a, price_b_eur_kg=price_b,
    )


# =====================================================================
# BERECHNUNG (zentral, einmal pro Render)
# =====================================================================

# Strompreis: LIVE → SMARD, Custom → eigene Eingabe, alle anderen → Strategy-Default
# _live_price_kwh ist in allen Zweigen (live / custom / standard-else) gesetzt
_elec_override = _live_price_kwh

result = compute_all(
    ee=ee, ely=ely, proc=proc, fleet=fleet, imp=imp,
    active_strategy=active_strategy,
    elec_price_override=_elec_override,
)


# =====================================================================
# Tab "Übersicht"
# =====================================================================

with tab_overview:
    # ── KPI-Karten Hilfsfunktionen ───────────────────────────
    def _kc(label, value, unit, color='#3b82f6', dim=False):
        _op = '0.4' if dim else '1'
        _bc = 'rgba(100,116,139,0.4)' if dim else color
        _vc = '#64748b' if dim else color
        return (
            f'<div style="background:#111827;border:1px solid rgba(255,255,255,0.07);'
            f'border-top:3px solid {_bc};border-radius:8px;padding:18px 20px;'
            f'min-height:105px;opacity:{_op};">'
            f'<div style="font-size:0.6rem;font-weight:600;letter-spacing:0.1em;'
            f'text-transform:uppercase;color:#6b7280;margin-bottom:8px;">{label}</div>'
            f'<div style="font-size:2rem;font-weight:700;color:{_vc};line-height:1;">{value}</div>'
            f'<div style="font-size:0.7rem;color:#4b5563;margin-top:6px;">{unit}</div>'
            f'</div>'
        )

    def _kc_hero(label, value, unit, sub, color='#3b82f6'):
        return (
            f'<div style="background:linear-gradient(135deg,rgba(30,58,95,0.6) 0%,#111827 100%);'
            f'border:1px solid rgba(59,130,246,0.25);border-top:4px solid {color};'
            f'border-radius:10px;padding:24px 28px;min-height:130px;position:relative;">'
            f'<div style="font-size:0.6rem;font-weight:700;letter-spacing:0.12em;'
            f'text-transform:uppercase;color:#6b7280;margin-bottom:10px;">{label}</div>'
            f'<div style="font-size:3rem;font-weight:800;color:{color};line-height:1;'
            f'letter-spacing:-0.03em;">{value}</div>'
            f'<div style="font-size:0.75rem;color:#94a3b8;margin-top:10px;">{unit}</div>'
            f'<div style="font-size:0.65rem;color:#374151;margin-top:4px;">{sub}</div>'
            f'</div>'
        )

    def _kc_secondary(label, value, unit, color='#3b82f6', dim=False):
        _op = '0.45' if dim else '1'
        _bc = 'rgba(100,116,139,0.3)' if dim else f'rgba(59,130,246,0.15)'
        _vc = '#64748b' if dim else color
        return (
            f'<div style="background:#0f172a;border:1px solid rgba(255,255,255,0.06);'
            f'border-left:3px solid {_vc};border-radius:8px;padding:14px 18px;'
            f'min-height:130px;opacity:{_op};">'
            f'<div style="font-size:0.58rem;font-weight:600;letter-spacing:0.1em;'
            f'text-transform:uppercase;color:#4b5563;margin-bottom:8px;">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:700;color:{_vc};line-height:1;">{value}</div>'
            f'<div style="font-size:0.68rem;color:#374151;margin-top:6px;">{unit}</div>'
            f'</div>'
        )

    # ── Seitenüberschrift ────────────────────────────────────
    st.markdown(
        '<p style="font-size:1.1rem;font-weight:700;color:#f1f5f9;'
        'margin-bottom:14px;margin-top:6px;letter-spacing:-0.01em;">'
        'Gesamtübersicht – LCOH &amp; TCO</p>',
        unsafe_allow_html=True,
    )

    # ── Zeile 1: Hero LCOH Gesamt + Sekundärkarten ───────────
    _lh2_dim = not proc.liq_on
    _lh2_val = f'{result.lcoh_total_lh2:.2f}' if proc.liq_on else 'n/a'
    _lh2_sub = 'LH₂-Pfad aktiv' if proc.liq_on else 'LH₂-Pfad nicht aktiv'
    _hero_sub = (f'{strat["icon"]} {strat["label"]} · '
                 f'{_elec_override*100:.1f} ct/kWh · {_live_flh:,} h/a · inkl. Prozesskette')
    _r1_hero, _r1_sec1, _r1_sec2 = st.columns([2, 1, 1])
    with _r1_hero:
        st.markdown(
            _kc_hero('LCOH Gesamt (CGH₂)', f'{result.lcoh_total_cgh2:.2f}', '€/kg H₂', _hero_sub, '#3b82f6'),
            unsafe_allow_html=True,
        )
    with _r1_sec1:
        st.markdown(
            _kc_secondary('LCOH Elektrolyse', f'{result.ely.lcoh_ely:.2f}', '€/kg H₂', '#60a5fa'),
            unsafe_allow_html=True,
        )
    with _r1_sec2:
        st.markdown(
            _kc_secondary('LH₂-Preis', _lh2_val, _lh2_sub, '#10b981', dim=_lh2_dim),
            unsafe_allow_html=True,
        )

    # ── Zeile 2: Betriebskennzahlen ──────────────────────────
    _r2c1, _r2c2, _r2c3 = st.columns(3)
    with _r2c1:
        st.markdown(
            _kc('Strompreis (EE-Mix)', f'{result.ee_blend.blended_eur_kwh*100:.2f}', 'ct/kWh · gewichteter Mix', '#f59e0b'),
            unsafe_allow_html=True,
        )
    with _r2c2:
        st.markdown(
            _kc('H₂-Produktion', f'{result.ely.ann_h2_kg/1000:,.1f}', 't H₂/a', '#e5e7eb'),
            unsafe_allow_html=True,
        )
    with _r2c3:
        _flh_range_str = (
            f'h/a · Range {strat["flhRange"]["min"]:,}–{strat["flhRange"]["max"]:,}'
            if active_strategy not in ('live', 'custom') else 'h/a'
        )
        st.markdown(
            _kc('Volllaststunden', f'{_live_flh:,}', _flh_range_str, '#e5e7eb'),
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── Zeile 3: Kostenstruktur (links) + LCOH-Verlauf (rechts) ──
    _ch_left, _ch_right = st.columns(2)

    # ── Linke Spalte: Kostenstruktur ─────────────────────────
    with _ch_left:
        st.markdown('<p class="section-heading">Kostenstruktur</p>', unsafe_allow_html=True)
        _bd = pd.DataFrame([
            {'K': 'Strom (Ely)',         'V': result.ely.elec_kg,  'C': '#10b981'},
            {'K': 'CAPEX Elektrolyseur', 'V': result.ely.capex_kg, 'C': '#3b82f6'},
            {'K': 'OPEX Elektrolyseur',  'V': result.ely.opex_kg,  'C': '#f59e0b'},
            {'K': 'Stack-Repl.',         'V': result.ely.stack_kg, 'C': '#6b7280'},
            {'K': 'Komprimierung',       'V': result.proc.comp,    'C': '#ef4444'},
            {'K': 'GH₂-Speicher',   'V': result.proc.stor,    'C': '#06b6d4'},
            {'K': 'Vertankung (CGH₂)', 'V': result.proc.disp, 'C': '#60a5fa'},
        ])
        if proc.liq_on:
            _bd = pd.concat([_bd, pd.DataFrame([
                {'K': 'Verflüssigung', 'V': result.proc.liq,     'C': '#8b5cf6'},
                {'K': 'LH₂-Speicher',  'V': result.proc.lh2_stor, 'C': '#a78bfa'},
                {'K': 'LH₂-Vertank.',  'V': result.proc.lh2_disp, 'C': '#c4b5fd'},
            ])], ignore_index=True)

        _fig_cost = go.Figure(go.Bar(
            x=_bd['V'], y=_bd['K'], orientation='h',
            marker_color=_bd['C'].tolist(),
            marker_line_width=0,
            text=[f'{v:.2f}' for v in _bd['V']],
            textposition='outside',
            textfont=dict(color='#cbd5e1', size=11),
            hovertemplate='<b>%{y}</b><br>%{x:.3f} €/kg<extra></extra>',
        ))
        _fig_cost.update_layout(
            height=380, showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis_title='€/kg H₂', yaxis_title='',
            margin=dict(l=0, r=55, t=10, b=0),
            font=dict(family='Inter', color='#94a3b8'),
            xaxis=dict(gridcolor='rgba(255,255,255,0.06)', zerolinecolor='rgba(59,130,246,0.15)'),
            yaxis=dict(gridcolor='rgba(255,255,255,0.04)', autorange='reversed'),
            hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        )
        st.plotly_chart(_fig_cost, use_container_width=True)

    # ── Rechte Spalte: LCOH-Verlauf hybrid ───────────────────
    with _ch_right:
        st.markdown('<p class="section-heading">LCOH-Verlauf: Hybride Betriebsstrategie</p>',
                    unsafe_allow_html=True)

        _base_flh_h = float(result.ely.flh_nom)
        _base_price = _elec_override
        _grid_p     = _sidebar_strom_eur_kwh
        _max_h      = 8760
        _step_h     = 100
        _xs_h = sorted(set(list(range(_step_h, _max_h, _step_h)) + [int(_base_flh_h), _max_h]))

        _ys_h    = []
        _ys_elec = []
        _ys_fix  = []
        for _th in _xs_h:
            if _th <= _base_flh_h:
                _wp = _base_price
            else:
                _wp = (_base_flh_h * _base_price + (_th - _base_flh_h) * _grid_p) / _th
            _te = ElyInputs(**{**ely.__dict__, "flh": float(_th)})
            _rr = compute_all(ee=ee, ely=_te, proc=proc, fleet=fleet, imp=imp,
                              active_strategy=active_strategy, elec_price_override=_wp)
            _ys_h.append(_rr.lcoh_total_cgh2)
            _ys_elec.append(_rr.ely.elec_kg)
            _ys_fix.append(_rr.ely.capex_kg + _rr.ely.opex_kg + _rr.ely.stack_kg)

        _base_idx     = _xs_h.index(int(_base_flh_h))
        _lcoh_at_base = _ys_h[_base_idx]
        _min_idx      = _ys_h.index(min(_ys_h))
        _min_x        = _xs_h[_min_idx]
        _min_y        = _ys_h[_min_idx]

        # Zwei Bereiche: EE (grün) und Netzstrom (orange)
        _ee_xs  = _xs_h[:_base_idx + 1]
        _ee_ys  = _ys_h[:_base_idx + 1]
        _net_xs = _xs_h[_base_idx:]
        _net_ys = _ys_h[_base_idx:]

        _fig_hyb = go.Figure()

        # Stromkosten-Fläche (ganzer Bereich)
        _fig_hyb.add_trace(go.Scatter(
            x=_xs_h, y=_ys_elec,
            name='Stromkosten-Anteil (€/kg)',
            mode='lines', line=dict(color='rgba(16,185,129,0.35)', width=1, dash='dot'),
            fill='tozeroy', fillcolor='rgba(16,185,129,0.06)',
            hovertemplate='Strom: %{y:.3f} €/kg<extra></extra>',
        ))
        # Fixkosten-Fläche (ganzer Bereich)
        _fig_hyb.add_trace(go.Scatter(
            x=_xs_h, y=_ys_fix,
            name='Fixkosten (€/kg)',
            mode='lines', line=dict(color='rgba(245,158,11,0.35)', width=1, dash='dot'),
            fill='tozeroy', fillcolor='rgba(245,158,11,0.04)',
            hovertemplate='Fixkosten: %{y:.3f} €/kg<extra></extra>',
        ))
        # EE-Bereich (grün)
        _fig_hyb.add_trace(go.Scatter(
            x=_ee_xs, y=_ee_ys,
            name=f"{strat['icon']} {strat['label']} – EE {_elec_override*100:.2f} ct/kWh · bis {_base_flh_h:,.0f} h/a",
            mode='lines+markers',
            line=dict(color='#10b981', width=3, shape='spline', smoothing=0.6),
            marker=dict(size=4, color='#f1f5f9', line=dict(color='#10b981', width=2)),
            hovertemplate='<b>%{x} h/a</b><br>LCOH: <b>%{y:.3f} €/kg</b><extra></extra>',
        ))
        # Netzstrom-Bereich (orange)
        _fig_hyb.add_trace(go.Scatter(
            x=_net_xs, y=_net_ys,
            name=f'Weiterbetrieb Netzstrom {_sb_strom_ct:.2f} ct/kWh',
            mode='lines+markers',
            line=dict(color='#f59e0b', width=3, shape='spline', smoothing=0.6),
            marker=dict(size=4, color='#f1f5f9', line=dict(color='#f59e0b', width=2)),
            hovertemplate='<b>%{x} h/a</b><br>LCOH: <b>%{y:.3f} €/kg</b><extra></extra>',
        ))
        # VLH-Markierung
        _fig_hyb.add_vline(
            x=_base_flh_h,
            line=dict(color='rgba(99,102,241,0.75)', width=2, dash='dash'),
            annotation_text=f'VLH: {_base_flh_h:,.0f} h · {_lcoh_at_base:.2f} €/kg',
            annotation_font_color='#818cf8', annotation_position='top right',
        )
        # Globales Minimum (wenn verschieden von VLH)
        if _min_idx != _base_idx:
            _fig_hyb.add_vline(
                x=_min_x,
                line=dict(color='rgba(16,185,129,0.7)', width=2, dash='dash'),
                annotation_text=f'Min: {_min_x:,} h · {_min_y:.2f} €/kg',
                annotation_font_color='#10b981',
                annotation_position='top left' if _min_x < _base_flh_h else 'top right',
            )
        # 8760 h Referenz
        _fig_hyb.add_hline(
            y=_ys_h[-1],
            line=dict(color='rgba(239,68,68,0.3)', width=1, dash='dot'),
            annotation_text=f'8760 h: {_ys_h[-1]:.2f} €/kg',
            annotation_font_color='#f87171', annotation_position='bottom right',
        )
        _fig_hyb.update_layout(
            height=380, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis_title='Betriebsstunden [h/a]',
            yaxis_title='LCOH [€/kg H₂]',
            font=dict(family='Inter', color='#94a3b8'),
            margin=dict(l=0, r=0, t=20, b=0),
            legend=dict(bgcolor='rgba(17,24,39,0.9)', bordercolor='rgba(255,255,255,0.08)',
                        borderwidth=1, font=dict(color='#94a3b8', size=9),
                        orientation='h', y=-0.26, x=0.5, xanchor='center'),
            xaxis=dict(gridcolor='rgba(255,255,255,0.06)', range=[0, _max_h + 100]),
            yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
            hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        )
        st.plotly_chart(_fig_hyb, use_container_width=True)
        st.markdown(
            f'<p style="font-size:0.68rem;color:#4b5563;margin-top:2px;">'
            f'EE-Betrieb bis {result.ely.flh_nom:,.0f} h/a zum Strategie-Preis '
            f'· danach Netzstrom ({_sb_strom_ct:.2f} ct/kWh)</p>',
            unsafe_allow_html=True,
        )

    # ==================================================================
    # TCO-Vergleich Fahrzeugflotte – aktive Strategie
    # ==================================================================
    st.markdown("---")
    st.markdown(
        '<p class="section-heading">Gestapelter TCO-Vergleich · aktive Strategie</p>',
        unsafe_allow_html=True,
    )

    _fdf = pd.DataFrame(result.fleet)
    _diesel_row = _fdf[_fdf['fuel'] == 'diesel']
    _d_tco_km   = float(_diesel_row['tcoPkm'].iloc[0]) if not _diesel_row.empty else 1.0

    # ── Stacked-Bar-Chart €/km ────────────────────────
    _stk_defs = [
        ('CAPEX ann.',    '#38bdf8', 'capexAnn'),
        ('Kraftstoff',    '#f59e0b', 'fuelCost'),
        ('Maut',          '#a78bfa', 'tollCost'),
        ('Vers.+Steuern', '#34d399', 'insurance'),
    ]
    _fig_stk = go.Figure()
    for _sname, _scol, _sfield in _stk_defs:
        _fig_stk.add_trace(go.Bar(
            name=_sname,
            x=_fdf['type'],
            y=_fdf[_sfield] / ann_km,
            marker_color=_scol,
            marker_line_width=0,
            hovertemplate=f'{_sname}: %{{y:.3f}} €/km<extra></extra>',
        ))
    _fig_stk.update_layout(
        barmode='stack', height=320,
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis_title='', yaxis_title='€/km',
        font=dict(family='Inter', color='#94a3b8'),
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(
            bgcolor='rgba(17,24,39,0.9)', bordercolor='rgba(255,255,255,0.07)',
            borderwidth=1, font=dict(color='#94a3b8', size=11),
            orientation='h', y=1.12, x=0.5, xanchor='center',
        ),
        xaxis=dict(gridcolor='rgba(255,255,255,0.04)'),
        yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        bargap=0.35,
    )
    st.plotly_chart(_fig_stk, use_container_width=True)

    # ── Detailtabelle ─────────────────────────────────────────────────────
    _ths = 'style="padding:9px 12px;font-size:0.6rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#6b7280;border-bottom:1px solid rgba(255,255,255,0.1);text-align:right;white-space:nowrap;"'
    _thl = 'style="padding:9px 12px;font-size:0.6rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#6b7280;border-bottom:1px solid rgba(255,255,255,0.1);text-align:left;"'
    _tds = 'style="padding:8px 12px;font-size:0.8rem;text-align:right;color:#94a3b8;border-bottom:1px solid rgba(255,255,255,0.04);"'
    _tdl = 'style="padding:8px 12px;font-size:0.8rem;text-align:left;font-weight:600;color:#e5e7eb;border-bottom:1px solid rgba(255,255,255,0.04);"'
    _tco_tbl = (
        '<div style="overflow-x:auto;margin-top:8px;">'
        '<table style="width:100%;border-collapse:collapse;background:#111827;border-radius:8px;overflow:hidden;">'
        '<thead><tr>'
        f'<th {_thl}>Fahrzeugtyp</th>'
        f'<th {_ths}>H₂ €/kg</th>'
        f'<th {_ths}>Kraftstoff (€/a)</th>'
        f'<th {_ths}>CAPEX Ann. (€/a)</th>'
        f'<th {_ths}>Maut (€/a)</th>'
        f'<th {_ths}>Vers.+St. (€/a)</th>'
        f'<th {_ths}>TCO Ges. (€/a)</th>'
        f'<th {_ths}>€/km</th>'
        f'<th {_ths}>vs. Diesel</th>'
        '</tr></thead><tbody>'
    )
    for _, _fr in _fdf.iterrows():
        _is_d   = _fr['fuel'] == 'diesel'
        _is_lh2 = _fr['fuel'] == 'h2_lh2'
        if _is_d:
            _h2_disp  = '–'
            _h2_color = '#4b5563'
        elif _is_lh2:
            _h2_disp  = f'{result.lcoh_total_lh2:.2f} €/kg' if proc.liq_on else 'n/a'
            _h2_color = '#3b82f6'
        else:
            _h2_disp  = f'{result.lcoh_total_cgh2:.2f} €/kg'
            _h2_color = '#3b82f6'
        _vs_pct = (_fr['tcoPkm'] - _d_tco_km) / _d_tco_km * 100 if _d_tco_km else 0.0
        _vs_col = '#10b981' if _vs_pct <= 0 else '#ef4444'
        _vs_str = f'{_vs_pct:+.1f}%'
        _tco_tbl += (
            '<tr>'
            f'<td {_tdl}>{_fr["type"]}</td>'
            f'<td style="padding:8px 12px;font-size:0.8rem;text-align:right;color:{_h2_color};font-weight:600;border-bottom:1px solid rgba(255,255,255,0.04);">{_h2_disp}</td>'
            f'<td {_tds}>{_fr["fuelCost"]/1000:.1f} k€</td>'
            f'<td {_tds}>{_fr["capexAnn"]/1000:.1f} k€</td>'
            f'<td {_tds}>{_fr["tollCost"]/1000:.1f} k€</td>'
            f'<td {_tds}>{_fr["insurance"]/1000:.1f} k€</td>'
            f'<td style="padding:8px 12px;font-size:0.8rem;text-align:right;color:#e5e7eb;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04);">{_fr["totalAnn"]/1000:.1f} k€</td>'
            f'<td style="padding:8px 12px;font-size:0.8rem;text-align:right;color:#3b82f6;font-weight:700;border-bottom:1px solid rgba(255,255,255,0.04);">{_fr["tcoPkm"]:.3f}</td>'
            f'<td style="padding:8px 12px;font-size:0.8rem;text-align:right;color:{_vs_col};font-weight:600;border-bottom:1px solid rgba(255,255,255,0.04);">{_vs_str}</td>'
            '</tr>'
        )
    _tco_tbl += '</tbody></table></div>'
    st.markdown(_tco_tbl, unsafe_allow_html=True)
    st.caption(
        f"Aktive Strategie: {strat['icon']} {strat['label']} · "
        f"H₂-Preis (CGH₂): {result.lcoh_total_cgh2:.2f} €/kg · "
        f"Jahreskilometer: {ann_km:,.0f} km/a · Dieselpreis: {diesel_price:.2f} €/l"
    )



# =====================================================================
# Tab "Strommix"
# =====================================================================

if tab_ee is not None:
    with tab_ee:
        st.markdown("---")
        eb = result.ee_blend
        c1, c2, c3 = st.columns(3)
        c1.metric("Mix-LCOE", f"{eb.blended_eur_kwh*100:.2f} ct/kWh")
        c2.metric("EE-Anteil", f"{eb.ee_share_pct:.0f}%")
        c3.metric("Netzanteil", f"{eb.grid_share_pct:.0f}%",
                  f"{eb.grid_price_eur_kwh*100:.1f} ct/kWh")

        _ee_left, _ee_right = st.columns([3, 2])

        with _ee_left:
            if eb.rows:
                st.markdown('<p class="section-heading">Aktiver Strommix</p>', unsafe_allow_html=True)
                _mix_df = pd.DataFrame(eb.rows)[['label', 'shareEff', 'capex', 'flh', 'lcoe']].copy()
                _mix_df.columns = ['Technologie', 'Anteil %', 'CAPEX €/kW', 'FLH h/a', 'LCOE €/kWh']
                _mix_df['Anteil %']    = _mix_df['Anteil %'].round(1)
                _mix_df['LCOE ct/kWh'] = (_mix_df['LCOE €/kWh'] * 100).round(2)
                _mix_df = _mix_df.drop(columns=['LCOE €/kWh'])
                if eb.grid_share_pct > 0:
                    _mix_df = pd.concat([_mix_df, pd.DataFrame([{
                        'Technologie': 'Netz', 'Anteil %': round(eb.grid_share_pct, 1),
                        'CAPEX €/kW': '–', 'FLH h/a': 8760,
                        'LCOE ct/kWh': round(eb.grid_price_eur_kwh * 100, 2),
                    }])], ignore_index=True)
                st.dataframe(_mix_df, use_container_width=True, hide_index=True)
            else:
                st.warning("Kein EE-Mix gewählt – nur Netzbezug aktiv.")

        with _ee_right:
            st.markdown('<p class="section-heading">Mix-Anteile</p>', unsafe_allow_html=True)
            _pie_labels, _pie_vals, _pie_colors = [], [], []
            _pal = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444']
            for _i, _r in enumerate(eb.rows):
                _pie_labels.append(_r['label'])
                _pie_vals.append(_r['shareEff'])
                _pie_colors.append(_pal[_i % len(_pal)])
            if eb.grid_share_pct > 0:
                _pie_labels.append('Netz')
                _pie_vals.append(eb.grid_share_pct)
                _pie_colors.append('#374151')
            if _pie_vals:
                _fig_pie = go.Figure(go.Pie(
                    labels=_pie_labels, values=_pie_vals,
                    hole=0.5, marker=dict(colors=_pie_colors,
                                          line=dict(color='#0b1120', width=2)),
                    textfont=dict(color='#cbd5e1', size=12),
                    hovertemplate='<b>%{label}</b><br>%{value:.1f}%<extra></extra>',
                ))
                _fig_pie.update_layout(
                    height=240, paper_bgcolor='rgba(0,0,0,0)',
                    margin=dict(l=0, r=0, t=0, b=0),
                    showlegend=True,
                    legend=dict(bgcolor='rgba(17,24,39,0.9)', bordercolor='rgba(255,255,255,0.07)',
                                borderwidth=1, font=dict(color='#94a3b8', size=11),
                                orientation='h', y=-0.15, x=0.5, xanchor='center'),
                    font=dict(family='Inter', color='#94a3b8'),
                    annotations=[dict(text=f"<b>{eb.blended_eur_kwh*100:.2f}</b><br>ct/kWh",
                                      x=0.5, y=0.5, font_size=14, font_color='#f1f5f9',
                                      showarrow=False)],
                )
                st.plotly_chart(_fig_pie, use_container_width=True)
            else:
                st.info("100 % Netzbezug")


# =====================================================================
# Tab "Elektrolyseur"
# =====================================================================

with tab_ely:
    st.markdown("---")
    er = result.ely

    # ── KPI-Zeile ──────────────────────────────────────────────
    _ek1, _ek2, _ek3, _ek4, _ek5 = st.columns(5)
    _ek1.metric("LCOH Elektrolyse",   f"{er.lcoh_ely:.2f} €/kg")
    _ek2.metric("Spez. Verbrauch",    f"{er.spec_eff_kwh_kg:.1f} kWh/kg",
                f"η {33.33/er.spec_eff_kwh_kg*100:.1f} %")
    _ek3.metric("Effektive FLH",      f"{er.flh_eff:,.0f} h/a",
                f"nom. {er.flh_nom:,.0f} h")
    _ek4.metric("Jahresproduktion",   f"{er.ann_h2_kg/1000:,.1f} t H₂/a")
    _ek5.metric("Jahresstrom",        f"{er.ann_elec_kwh/1e6:,.1f} GWh")

    st.markdown("")

    # ── Charts ─────────────────────────────────────────────────
    _el, _er_ = st.columns([1, 1])

    with _el:
        st.markdown('<p class="section-heading">Kostenstruktur je kg H₂</p>',
                    unsafe_allow_html=True)
        _pie_df = pd.DataFrame([
            {'Komponente': 'Strom',        'Wert': er.elec_kg,  'Farbe': '#3b82f6'},
            {'Komponente': 'CAPEX',        'Wert': er.capex_kg, 'Farbe': '#1d4ed8'},
            {'Komponente': 'OPEX',         'Wert': er.opex_kg,  'Farbe': '#8b5cf6'},
            {'Komponente': 'Stack-Ersatz', 'Wert': er.stack_kg, 'Farbe': '#f59e0b'},
        ])
        _fig_pie = go.Figure(go.Pie(
            labels=_pie_df['Komponente'], values=_pie_df['Wert'].round(4),
            hole=0.58, marker=dict(colors=_pie_df['Farbe'].tolist(),
                                   line=dict(color='#0b1120', width=2)),
            textinfo='label+percent', textposition='outside',
            textfont=dict(color='#cbd5e1', size=11),
            pull=[0.04, 0.02, 0.02, 0.02],
            hovertemplate='<b>%{label}</b><br>%{value:.3f} €/kg<br>%{percent}<extra></extra>',
        ))
        _fig_pie.update_layout(
            height=300, paper_bgcolor='rgba(0,0,0,0)',
            showlegend=False,
            margin=dict(l=10, r=10, t=10, b=10),
            font=dict(family='Inter', color='#94a3b8'),
            hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
            annotations=[dict(
                text=f"<b>{er.lcoh_ely:.2f}</b><br><span style='font-size:11px'>€/kg</span>",
                x=0.5, y=0.5, font_size=18, font_color='#f1f5f9',
                showarrow=False, align='center',
            )],
        )
        st.plotly_chart(_fig_pie, use_container_width=True)

    with _er_:
        st.markdown('<p class="section-heading">Kostendetail</p>', unsafe_allow_html=True)
        # Horizontaler Balken je Kostenkomponente
        _bar_df = _pie_df.copy()
        _bar_df['Label'] = _bar_df.apply(
            lambda r: f"{r['Wert']:.3f} €/kg ({r['Wert']/er.lcoh_ely*100:.0f} %)", axis=1)
        _fig_bar = go.Figure(go.Bar(
            x=_bar_df['Wert'], y=_bar_df['Komponente'], orientation='h',
            marker_color=_bar_df['Farbe'].tolist(), marker_line_width=0,
            text=_bar_df['Label'], textposition='outside',
            textfont=dict(color='#94a3b8', size=11),
            hovertemplate='<b>%{y}</b>: %{x:.4f} €/kg<extra></extra>',
        ))
        _fig_bar.update_layout(
            height=300, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis_title='€/kg H₂', yaxis_title='',
            font=dict(family='Inter', color='#94a3b8'),
            margin=dict(l=0, r=120, t=10, b=10),
            xaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
            yaxis=dict(gridcolor='rgba(0,0,0,0)'),
            hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        )
        st.plotly_chart(_fig_bar, use_container_width=True)

    # ── Finanz-Zusammenfassung ──────────────────────────────────
    with st.expander("📊 Finanzielle Kennzahlen", expanded=False):
        _fk1, _fk2, _fk3, _fk4 = st.columns(4)
        _total_invest = ely.power_mw * 1000 * ely.capex_eur_kw
        _ann_opex_eur = _total_invest * ely.opex_pct / 100
        _n = max(ely.life_a, 1)
        _w = ely.wacc_pct / 100
        _crf_val = (_w * (1+_w)**_n) / ((1+_w)**_n - 1) if _w > 0 else 1/_n
        _ann_cap = _total_invest * _crf_val
        _fk1.metric("Investition gesamt",   f"{_total_invest/1e6:.1f} M€",
                    f"{ely.power_mw:.1f} MW · {ely.capex_eur_kw:.0f} €/kW")
        _fk2.metric("Kapitalkosten/a",      f"{_ann_cap/1e3:.0f} k€/a",
                    f"CRF {_crf_val*100:.2f} % · {ely.wacc_pct} % WACC")
        _fk3.metric("OPEX/a",               f"{_ann_opex_eur/1e3:.0f} k€/a",
                    f"{ely.opex_pct} % des CAPEX")
        _fk4.metric("CAPEX/kg H₂",          f"{er.capex_kg:.3f} €/kg",
                    f"bei {er.flh_eff:,.0f} h/a effektiv")


# =====================================================================
# Tab "Prozesskette" – Ergebnisse + Flowchart mit Kostenwerten
# =====================================================================

with tab_proc:
    st.markdown("---")
    pr = result.proc

    # ── Flowchart mit Kostenwerten (Zwei-Pfad) ──
    _ss2 = st.session_state.proc_step

    def _rn(key, icon, label, cost, is_lh2=False):
        sel_cls = ' sel' if (_ss2 == key) else ''
        lh2_cls = ' lh2' if is_lh2 else ''
        dim_cls = ' dim' if (is_lh2 and not proc.liq_on) else ''
        return (f'<td class="fn{sel_cls}{lh2_cls}{dim_cls}">'
                f'<span class="fi">{icon}</span>'
                f'<span class="fl">{label}</span>'
                f'<span class="fc">{cost}</span></td>')

    _shared2 = ('background:#111827;'
                'border:1px solid rgba(255,255,255,0.08);border-top:2px solid #3b82f6;border-radius:6px;'
                'text-align:center;padding:10px 10px;min-width:80px;vertical-align:middle;')

    st.markdown(textwrap.dedent(f"""
    <div class="flow-wrap">
    <table class="flow-tbl">
      <tr>
        <td rowspan="2" style="{_shared2}">
          <span style="font-size:1.25rem;">⚡</span><br>
          <span style="font-size:0.68rem;font-weight:600;color:#3b82f6;">ELY</span><br>
          <span style="font-size:0.82rem;font-weight:700;color:#3b82f6;">{result.ely.lcoh_ely:.2f} €/kg</span>
        </td>
        <td rowspan="2" class="fa">→</td>
        <td rowspan="2" style="{_shared2}">
          <span style="font-size:1.25rem;">📦</span><br>
          <span style="font-size:0.68rem;font-weight:600;color:#94a3b8;">GH₂-Puffer</span><br>
          <span style="font-size:0.75rem;color:#64748b;">{pr.mfr_ely_peak:.0f} kg/h</span>
        </td>
        <td rowspan="2" class="fb">─┬<br>&nbsp;└</td>
        {_rn('comp','🔧','Kompressor',f'{pr.comp:.3f} €/kg')}
        <td class="fa">→</td>
        {_rn('stor','📦','CGH₂-Speicher',f'{pr.stor:.3f} €/kg')}
        <td class="fa">→</td>
        {_rn('disp','⛽','Vertankung',f'{pr.disp:.3f} €/kg')}
        <td style="padding-left:6px;">
          <span class="lane-tag" style="background:rgba(59,130,246,0.1);color:#3b82f6;border:1px solid rgba(59,130,246,0.2);">CGH₂<br><small style="font-size:0.75em;">{result.lcoh_total_cgh2:.2f} €/kg</small></span>
        </td>
      </tr>
      <tr>
        {_rn('lh2','❄️','Verflüssigung',f'{pr.liq:.3f} €/kg' if proc.liq_on else '—',is_lh2=True)}
        <td class="fa">→</td>
        {_rn('lh2','🧊','LH₂-Speicher',f'{pr.lh2_stor:.3f} €/kg' if proc.liq_on else '—',is_lh2=True)}
        <td class="fa">→</td>
        {_rn('lh2','⛽','LH₂-Vertankung',f'{pr.lh2_disp:.3f} €/kg' if proc.liq_on else '—',is_lh2=True)}
        <td style="padding-left:6px;">
          <span class="lane-tag" style="{'opacity:0.4;' if not proc.liq_on else ''}background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.2);">LH₂{'<br><small style=\"font-size:0.75em;\">' + f'{result.lcoh_total_lh2:.2f} €/kg</small>' if proc.liq_on else ''}</span>
        </td>
      </tr>
    </table>
    </div>
    """), unsafe_allow_html=True)

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    c1.metric("Kompression", f"{pr.comp:.3f} €/kg",
              f"{pr.comp_energy_kwh_kg:.2f} kWh/kg · Auslegung: {pr.mfr_kgh:.1f} kg/h")
    st.caption(
        f"Kompressor-Auslegung: **{pr.mfr_kgh:.1f} kg/h** "
        f"({proc.comp_util_target_pct:.0f} % Zielauslastung · {8760 * proc.comp_util_target_pct / 100:.0f} h/a) · "
        f"CAPEX-Anlage: **{pr.mfr_kgh * proc.comp_capex_eur_kgh / 1000:.0f} k€** · "
        f"ELY-Nennstrom: {pr.mfr_ely_peak:.1f} kg/h"
    )
    c2.metric("Speicher (CGH₂)", f"{pr.stor:.3f} €/kg")
    c3.metric("Vertankung", f"{pr.disp:.3f} €/kg")

    if proc.liq_on:
        c1, c2, c3 = st.columns(3)
        c1.metric("Verflüssigung", f"{pr.liq:.3f} €/kg")
        c2.metric("LH₂-Speicher", f"{pr.lh2_stor:.3f} €/kg")
        c3.metric("LH₂-Vertankung", f"{pr.lh2_disp:.3f} €/kg")
        st.metric("LH₂-Pfad gesamt", f"{pr.lh2_total:.3f} €/kg")
        _liq_capex_total = pr.liq_mfr_kgh * proc.liq_capex_eur_kgh / 1_000_000
        st.caption(
            f"Verflüssiger-Auslegung: **{pr.liq_mfr_kgh:.1f} kg/h** "
            f"({proc.liq_util_target_pct:.0f} % Zielauslastung · {8760 * proc.liq_util_target_pct / 100:.0f} h/a) · "
            f"CAPEX-Anlage: **{_liq_capex_total:.2f} Mio. €** · "
            f"ELY-Nennstrom zum Vergleich: {pr.mfr_kgh:.1f} kg/h"
        )


# =====================================================================
# Tab "Flotte"
# =====================================================================

with tab_fleet:
    st.markdown("---")
    df = pd.DataFrame(result.fleet)
    show = df[['type', 'price', 'fuel', 'cons_100km', 'fuelCost',
               'tollCost', 'insurance', 'capexAnn', 'totalAnn', 'tcoPkm']].copy()
    show.columns = ['Typ', 'Preis €', 'Kraftstoff', 'Verbr./100km',
                    'Sprit €/a', 'Maut €/a', 'Versich. €/a',
                    'CAPEX-Ann. €/a', 'Gesamt €/a', 'TCO €/km']
    for c in show.columns[1:]:
        if show[c].dtype != 'object':
            show[c] = show[c].round(2)
    st.dataframe(show, use_container_width=True, hide_index=True)

    fig = px.bar(df, x='type', y='tcoPkm', color='fuel',
                 text=df['tcoPkm'].round(3),
                 color_discrete_map={'diesel': '#f59e0b', 'h2_ch2': '#3b82f6', 'h2_lh2': '#10b981'})
    fig.update_traces(
        textposition='outside',
        marker_line_width=0,
        textfont=dict(color='#cbd5e1', size=11),
    )
    fig.update_layout(
        height=380, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis_title='', yaxis_title='TCO [€/km]',
        margin=dict(l=0, r=0, t=20, b=0),
        font=dict(family='Inter', color='#94a3b8'),
        legend=dict(
            bgcolor='rgba(17,24,39,0.9)', bordercolor='rgba(255,255,255,0.07)',
            borderwidth=1, font=dict(color='#94a3b8', size=11),
        ),
        xaxis=dict(gridcolor='rgba(255,255,255,0.04)'),
        yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        bargap=0.35,
    )
    st.plotly_chart(fig, use_container_width=True)


# =====================================================================
# Tab "Import-Vergleich"
# =====================================================================

with tab_import:
    st.markdown("---")
    ir = result.import_

    # ── KPIs Schifffahrt ───────────────────────────────────────
    st.info(
        "**Preismodell:** Der Basispreis (A/B) ist der H₂-Preis **frei Importterminal** "
        "– er enthält bereits Produktion, Seefracht, NH₃-Synthese und Reconversion (Cracking). "
        "Der ausgewiesene Transport ist der **Lkw-Transport Hafen → Verbraucher**.",
        icon="ℹ️",
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("🚚 Transport Hafen→Verbraucher", f"{ir['transport_eur_kg']:.2f} €/kg",
              f"{ir['distance_km']:.0f} km Lkw (Tube-Trailer)")
    c2.metric("🚢 Import A (Markt)",     f"{ir['total_a_eur_kg']:.2f} €/kg",
              f"Terminal {ir['price_a']:.2f} €/kg + Transport")
    c3.metric("🚢 Import B (de-risked)", f"{ir['total_b_eur_kg']:.2f} €/kg",
              f"Terminal {ir['price_b']:.2f} €/kg + Transport")

    st.markdown("---")

    # ── Pipeline Rotterdam ─────────────────────────────────────
    st.markdown('<p class="section-heading">🔧 Pipeline-Import · Rotterdam → Standort</p>',
                unsafe_allow_html=True)
    st.caption(f"H₂-Quellenpreis Rotterdam = Import A (Markt): **{price_a:.2f} €/kg**")
    _pi1, _pi2 = st.columns(2)
    with _pi1:
        pipe_dist = st.slider(
            "Pipelinedistanz [km]", 50, 2000, 400, 50, key="pipe_dist",
        )
    with _pi2:
        pipe_tariff = st.number_input(
            "Tarif etablierte Pipeline [€/kg·100km]", 0.05, 2.0, 0.18, 0.01,
            key="pipe_tariff",
            help="Repurposed Gaspipeline: ca. 0.10–0.25 €/kg·100km · Neubau: 0.30–0.80 €/kg·100km",
        )
    pipe_source     = price_a   # Rotterdam-Quellenpreis = Import A (Markt)
    _pipe_transport = pipe_tariff * pipe_dist / 100
    _pipe_total     = pipe_source + _pipe_transport
    _pp1, _pp2, _pp3 = st.columns(3)
    _pp1.metric("Pipeline-Transport",  f"{_pipe_transport:.2f} €/kg",
                f"{pipe_dist} km · {pipe_tariff:.2f} €/kg·100km")
    _pp2.metric("H₂-Preis Rotterdam",  f"{pipe_source:.2f} €/kg",
                "= Import A (Markt)")
    _pp3.metric("🔧 Pipeline gesamt",   f"{_pipe_total:.2f} €/kg",
                f"{_pipe_total - result.lcoh_total_cgh2:+.2f} vs. Eigenproduktion")

    st.markdown("---")

    # ── Vergleichschart alle Quellen ───────────────────────────
    st.markdown('<p class="section-heading">Kostenvergleich alle Versorgungsoptionen</p>',
                unsafe_allow_html=True)

    _cmp_labels, _cmp_vals, _cmp_cols, _cmp_grp = [], [], [], []

    # Eigenproduktion (Modell, alle Strategien)
    for _sk, _sd in STRATEGY_PARAMS.items():
        _tmp = ElyInputs(**{**ely.__dict__, 'flh': _sd['flh']})
        _rr  = compute_all(ee=ee, ely=_tmp, proc=proc, fleet=fleet, imp=imp,
                           active_strategy=_sk, elec_price_override=_sd['eePrice'])
        _cmp_labels.append(f"{_sd['icon']} {_sd['label']}")
        _cmp_vals.append(round(_rr.lcoh_total_cgh2, 3))
        _cmp_cols.append('#3b82f6' if _sk == active_strategy else '#1e3a5f')
        _cmp_grp.append('Eigenproduktion')

    # Schifffahrt
    _cmp_labels += ['🚢 Import A (Markt)', '🚢 Import B (de-risked)']
    _cmp_vals   += [round(ir['total_a_eur_kg'], 3), round(ir['total_b_eur_kg'], 3)]
    _cmp_cols   += ['#f59e0b', '#d97706']
    _cmp_grp    += ['Schifffahrt', 'Schifffahrt']

    # Pipeline
    _cmp_labels.append(f"🔧 Pipeline Rotterdam ({pipe_dist} km)")
    _cmp_vals.append(round(_pipe_total, 3))
    _cmp_cols.append('#10b981')
    _cmp_grp.append('Pipeline')

    _fig_cmp = go.Figure(go.Bar(
        x=_cmp_labels, y=_cmp_vals,
        marker_color=_cmp_cols, marker_line_width=0,
        text=[f"{v:.2f}" for v in _cmp_vals],
        textposition='outside', textfont=dict(color='#cbd5e1', size=11),
        hovertemplate='<b>%{x}</b><br>%{y:.3f} €/kg H₂<extra></extra>',
    ))
    # Referenzlinie: aktuelle Eigenproduktion
    _fig_cmp.add_hline(
        y=result.lcoh_total_cgh2,
        line=dict(color='rgba(59,130,246,0.6)', width=1.5, dash='dash'),
        annotation_text=f"Eigenprod. aktuell: {result.lcoh_total_cgh2:.2f} €/kg",
        annotation_font_color='#60a5fa', annotation_position='bottom right',
    )
    _fig_cmp.update_layout(
        height=380, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis_title='', yaxis_title='Kosten [€/kg H₂]',
        font=dict(family='Inter', color='#94a3b8'),
        margin=dict(l=0, r=0, t=20, b=0),
        xaxis=dict(gridcolor='rgba(255,255,255,0.04)', tickangle=-25),
        yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
        bargap=0.3,
    )
    st.plotly_chart(_fig_cmp, use_container_width=True)
    st.caption("Eigenproduktion: vollständiger Modell-LCOH inkl. Prozesskette · "
               "Pipeline: Marktpreis Rotterdam + Transporttarif · "
               "Schifffahrt: NH₃-Reconversionsroute")


# =====================================================================
# Tab "Sensitivität"
# =====================================================================

with tab_sens:
    st.markdown('<p class="section-heading">Tornado-Diagramm · Einfluss aller Variablen auf LCOH</p>',
                unsafe_allow_html=True)

    # ── Tornado: alle Variablen ±Bereich ──────────────────────
    _base_lcoh = result.lcoh_total_cgh2
    _ep = _elec_override  # aktueller Strompreis

    def _sens_range(field, lo, hi, steps=8, elec_override=None):
        """LCOH bei lo und hi für ein ElyInputs-Feld."""
        def _calc(v):
            _te = ElyInputs(**{**ely.__dict__, field: v})
            return compute_all(ee=ee, ely=_te, proc=proc, fleet=fleet, imp=imp,
                               active_strategy=active_strategy,
                               elec_price_override=elec_override or _ep).lcoh_total_cgh2
        return _calc(lo), _calc(hi)

    def _sens_elec(lo_ct, hi_ct):
        def _c(v): return compute_all(ee=ee, ely=ely, proc=proc, fleet=fleet, imp=imp,
                                      active_strategy=active_strategy,
                                      elec_price_override=v).lcoh_total_cgh2
        return _c(lo_ct/100), _c(hi_ct/100)

    _tornado_defs = [
        ("Strompreis",         f"{_ep*100:.1f} ct/kWh",  *_sens_elec(2, 20)),
        ("Volllaststunden",    f"{ely.flh:,.0f} h/a",    *_sens_range('flh', 1000, 8000)),
        ("ELY-CAPEX",          f"{ely.capex_eur_kw:.0f} €/kW", *_sens_range('capex_eur_kw', 400, 2000)),
        ("Spez. Verbrauch",    f"{ely.specific_kwh_kg:.1f} kWh/kg", *_sens_range('specific_kwh_kg', 45, 65)),
        ("WACC",               f"{ely.wacc_pct:.1f} %",  *_sens_range('wacc_pct', 3, 12)),
        ("ELY-OPEX",           f"{ely.opex_pct:.1f} %/a",*_sens_range('opex_pct', 1.0, 7.0)),
        ("Lebensdauer",        f"{ely.life_a:.0f} a",    *_sens_range('life_a', 8, 30)),
        ("Stack-Lebensdauer",  f"{ely.stack_life_h/1000:.0f}k h", *_sens_range('stack_life_h', 20_000, 160_000)),
        ("Anlagenleistung",    f"{ely.power_mw:.1f} MW", *_sens_range('power_mw', 0.5, 50.0)),
    ]
    # Sortieren nach Spannweite absteigend
    _tornado_defs.sort(key=lambda r: abs(r[3] - r[2]), reverse=True)

    _t_names  = [r[0] for r in _tornado_defs]
    _t_cur    = [r[1] for r in _tornado_defs]
    _t_lo     = [r[2] for r in _tornado_defs]
    _t_hi     = [r[3] for r in _tornado_defs]
    _t_lo_d   = [v - _base_lcoh for v in _t_lo]
    _t_hi_d   = [v - _base_lcoh for v in _t_hi]

    _fig_torn = go.Figure()
    _fig_torn.add_trace(go.Bar(
        name='Niedrig', y=_t_names, x=_t_lo_d, orientation='h',
        marker_color='rgba(59,130,246,0.75)', marker_line_width=0,
        base=_base_lcoh,
        hovertemplate='<b>%{y}</b><br>LCOH: %{base:.3f} €/kg (%{x:+.3f})<extra></extra>',
    ))
    _fig_torn.add_trace(go.Bar(
        name='Hoch', y=_t_names, x=_t_hi_d, orientation='h',
        marker_color='rgba(239,68,68,0.75)', marker_line_width=0,
        base=_base_lcoh,
        hovertemplate='<b>%{y}</b><br>LCOH: %{base:.3f} €/kg (%{x:+.3f})<extra></extra>',
    ))
    _fig_torn.add_vline(
        x=_base_lcoh,
        line=dict(color='rgba(245,158,11,0.8)', width=2, dash='dot'),
        annotation_text=f"Basis: {_base_lcoh:.2f}",
        annotation_font_color='#f59e0b', annotation_position='top',
    )
    _fig_torn.update_layout(
        height=420, barmode='overlay', plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis_title='LCOH [€/kg H₂]', yaxis_title='',
        font=dict(family='Inter', color='#94a3b8'),
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(bgcolor='rgba(17,24,39,0.9)', bordercolor='rgba(255,255,255,0.07)',
                    borderwidth=1, font=dict(color='#94a3b8', size=11),
                    orientation='h', y=-0.12, x=0.5, xanchor='center'),
        xaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        yaxis=dict(gridcolor='rgba(0,0,0,0)', autorange='reversed'),
        hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
    )
    st.plotly_chart(_fig_torn, use_container_width=True)
    st.caption("Blau = Unteres Ende des Parameterbereichs · Rot = Oberes Ende · "
               "Breite = Sensitivität auf LCOH")

    st.markdown("---")
    st.markdown('<p class="section-heading">Einzelne Variable im Detail</p>',
                unsafe_allow_html=True)

    param = st.selectbox(
        "Variable",
        ["Strompreis [ct/kWh]", "FLH [h/a]", "ELY-CAPEX [€/kW]", "WACC [%]",
         "Spez. Verbrauch [kWh/kg]", "ELY-OPEX [%/a]", "Lebensdauer [a]",
         "Stack-Lebensdauer [1000 h]", "Anlagenleistung [MW]",
         "Kompressor-CAPEX [€/(kg/h)]", "Speicher-CAPEX [€/kg]"],
        key="sens_param",
    )

    if param == "Strompreis [ct/kWh]":
        xs = [v/2 for v in range(2, 43)]   # 1–21 ct/kWh in 0.5-Schritten
        ys = [compute_all(ee=ee, ely=ely, proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=x/100).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = _ep * 100, "Strompreis [ct/kWh]"
    elif param == "FLH [h/a]":
        xs = list(range(500, 8761, 250))
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'flh': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.flh, "Volllaststunden [h/a]"
    elif param == "ELY-CAPEX [€/kW]":
        xs = list(range(300, 2201, 100))
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'capex_eur_kw': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.capex_eur_kw, "ELY-CAPEX [€/kW]"
    elif param == "WACC [%]":
        xs = [w/4 for w in range(4, 53)]   # 1–13 % in 0.25-Schritten
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'wacc_pct': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.wacc_pct, "WACC [%]"
    elif param == "Spez. Verbrauch [kWh/kg]":
        xs = list(range(42, 68, 1))
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'specific_kwh_kg': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.specific_kwh_kg, "Spez. Verbrauch [kWh/kg]"
    elif param == "ELY-OPEX [%/a]":
        xs = [v/4 for v in range(2, 33)]   # 0.5–8 %
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'opex_pct': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.opex_pct, "ELY-OPEX [%/a]"
    elif param == "Lebensdauer [a]":
        xs = list(range(8, 31, 1))
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'life_a': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.life_a, "ELY-Lebensdauer [a]"
    elif param == "Stack-Lebensdauer [1000 h]":
        xs = list(range(20, 161, 10))   # 20k–160k h
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'stack_life_h': x*1000}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.stack_life_h / 1000, "Stack-Lebensdauer [1.000 h]"
    elif param == "Anlagenleistung [MW]":
        xs = [0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 75, 100]
        ys = [compute_all(ee=ee, ely=ElyInputs(**{**ely.__dict__, 'power_mw': x}),
                          proc=proc, fleet=fleet, imp=imp,
                          active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = ely.power_mw, "Anlagenleistung [MW]"
    elif param == "Kompressor-CAPEX [€/(kg/h)]":
        xs = list(range(5000, 85001, 5000))
        ys = [compute_all(ee=ee, ely=ely,
                          proc=ProcInputs(**{**proc.__dict__, 'comp_capex_eur_kgh': x}),
                          fleet=fleet, imp=imp, active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = proc.comp_capex_eur_kgh, "Kompressor-CAPEX [€/(kg/h)]"
    else:  # Speicher-CAPEX
        xs = list(range(100, 2601, 100))
        ys = [compute_all(ee=ee, ely=ely,
                          proc=ProcInputs(**{**proc.__dict__, 'stor_capex_eur_kg': x}),
                          fleet=fleet, imp=imp, active_strategy=active_strategy,
                          elec_price_override=_ep).lcoh_total_cgh2 for x in xs]
        cur_x, xtitle = proc.stor_capex_eur_kg, "Speicher-CAPEX [€/kg]"

    _fig_line = go.Figure()
    _fig_line.add_trace(go.Scatter(
        x=xs, y=ys, mode='lines+markers',
        line=dict(color='#3b82f6', width=3, shape='spline', smoothing=0.7),
        marker=dict(size=5, color='#f1f5f9', line=dict(color='#3b82f6', width=2)),
        fill='tozeroy', fillcolor='rgba(59,130,246,0.05)',
        hovertemplate='<b>%{x}</b><br>LCOH: <b>%{y:.3f} €/kg</b><extra></extra>',
    ))
    _fig_line.add_vline(
        x=cur_x,
        line=dict(color='rgba(245,158,11,0.7)', width=2, dash='dash'),
        annotation_text=f"Aktuell: {_base_lcoh:.2f} €/kg",
        annotation_font_color='#f59e0b', annotation_position='top right',
    )
    _fig_line.update_layout(
        xaxis_title=xtitle, yaxis_title='LCOH [€/kg H₂]',
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        height=360, margin=dict(l=0, r=0, t=20, b=0),
        font=dict(family='Inter', color='#94a3b8'),
        xaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        yaxis=dict(gridcolor='rgba(255,255,255,0.06)'),
        hoverlabel=dict(bgcolor='#1e293b', bordercolor='#3b82f6', font_color='#f1f5f9'),
    )
    st.plotly_chart(_fig_line, use_container_width=True)
    st.caption(
        f"Aktuell: **{_base_lcoh:.2f} €/kg** · "
        f"Spannweite: **{min(ys):.2f} – {max(ys):.2f} €/kg** · "
        f"Δ **{max(ys)-min(ys):.2f} €/kg**"
    )


# =====================================================================
# Footer
# =====================================================================

st.markdown("""
<div style="
    margin-top: 40px;
    border-top: 1px solid rgba(255,255,255,0.07);
    padding-top: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
">
    <span style="font-size:0.75rem; color:#475569;">
        🐍 H₂ Elektrolyse Preisrechner &nbsp;·&nbsp; Python-Port von H2_Preisrechner_v20_SMARD
    </span>
    <span style="font-size:0.75rem; color:#334155;">
        Modell vereinfacht &nbsp;·&nbsp; Updated May 2026 defaults
    </span>
</div>
""", unsafe_allow_html=True)
