"""
H₂ Elektrolyse Preisrechner – Rechenkern
=========================================
Reines Berechnungsmodul ohne UI-Abhängigkeiten.
Portiert aus H2_Preisrechner_v20_SMARD (HTML/JS, v13.2-stable).

Konventionen:
- Preise in €/kWh (intern), Anzeige oft in €/MWh oder ct/kWh
- LCOH in €/kg H₂
- Energien in kWh, Massen in kg
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from math import pow as mpow
from typing import Optional


# =====================================================================
# KONSTANTEN & STAMMDATEN (1:1 aus HTML übernommen)
# =====================================================================

EE_DEFAULTS: dict[str, dict[str, float]] = {
    'Photovoltaik':  {'capex': 700,  'opex': 1.5, 'life': 25, 'flh': 1050},
    'Wind Onshore':  {'capex': 1400, 'opex': 2.5, 'life': 20, 'flh': 2000},
    'Wind Offshore': {'capex': 3000, 'opex': 3.0, 'life': 25, 'flh': 4200},
    'Wasserkraft':   {'capex': 2500, 'opex': 2.0, 'life': 40, 'flh': 4500},
}

EE_MIX_PRESETS: dict[str, list[tuple[str, float]]] = {
    'Kein Eigenstrom':           [],
    '100% PV':                   [('Photovoltaik', 100)],
    '50% PV + 50% Wind Onshore': [('Photovoltaik', 50), ('Wind Onshore', 50)],
    '70% PV + 30% Wind Onshore': [('Photovoltaik', 70), ('Wind Onshore', 30)],
}

STRATEGY_PARAMS: dict[str, dict] = {
    'ee_abg': {
        'label': 'EE Abgeschrieben', 'icon': '♻️', 'color': '#22c55e',
        'co2': '✅ RFNBO', 'driver': 'CAPEX Elektrolyseur',
        'priceRange': {'low': 20, 'mid': 27.5, 'high': 35},
        'eePrice': 0.0275, 'flh': 3000,
        'flhRange': {'min': 1000, 'max': 4000},
        'annH2Kg': 262500, 'storKg': 1800,
        'compKgh': 35, 'dispKgh': 35, 'liqKgh': 25, 'storDays': 0.5,
        'desc': '20–35 €/MWh · 1.000–4.000 h/a',
    },
    'liefv': {
        'label': 'Liefervertrag', 'icon': '📋', 'color': '#38bdf8',
        'co2': '✅ RFNBO (Nachweis)', 'driver': 'Strom + CAPEX',
        'priceRange': {'low': 60, 'mid': 75, 'high': 90},
        'eePrice': 0.075, 'flh': 3000,
        'flhRange': {'min': 3000, 'max': 4500},
        'annH2Kg': 250000, 'storKg': 1800,
        'compKgh': 35, 'dispKgh': 35, 'liqKgh': 35, 'storDays': 0.5,
        'desc': '60–90 €/MWh · 3.000–4.500 h/a',
    },
    'spot': {
        'label': 'Spotmarkt', 'icon': '📈', 'color': '#f59e0b',
        'co2': '⚠️ stündl. Matching', 'driver': 'Spot + Speicher',
        'priceRange': {'low': 70, 'mid': 95, 'high': 120},
        'eePrice': 0.095, 'flh': 4000,
        'flhRange': {'min': 3500, 'max': 5000},
        'annH2Kg': 350000, 'storKg': 1800,
        'compKgh': 50, 'dispKgh': 50, 'liqKgh': 50, 'storDays': 0.5,
        'desc': '70–120 €/MWh · 3.500–5.000 h/a',
    },
    'grund': {
        'label': 'Grundlast', 'icon': '🏭', 'color': '#a78bfa',
        'co2': '❌ kein Grün-H₂', 'driver': 'Stromkosten',
        'priceRange': {'low': 140, 'mid': 150, 'high': 160},
        'eePrice': 0.15, 'flh': 8000,
        'flhRange': {'min': 8000, 'max': 8760},
        'annH2Kg': 650000, 'storKg': 1800,
        'compKgh': 90, 'dispKgh': 90, 'liqKgh': 90, 'storDays': 0.5,
        'desc': '140–160 €/MWh · >8.000 h/a',
    },
}

SCENARIO_PARAMS: dict[str, dict] = {
    'konservativ': {
        'label': 'Konservativ',
        'elyCAPEX': 1400, 'elyOPEX': 3.0, 'elyLife': 20, 'elyEta': 54, 'elyWACC': 7.0,
        'compEta': 70, 'compCAPEX': 10000, 'compOPEX': 4.0, 'compLife': 12,
        'storCAPEX': 1100, 'storOPEX': 2.5, 'storLife': 20,
        'dispCAPEX': 600,  'dispUtil': 70,  'dispOPEX': 2.5, 'dispLife': 15,
        'liqSEC': 15.0, 'liqCAPEX': 160_000, 'liqOPEX': 5.0, 'liqLife': 20,
        'lh2Days': 0.5, 'lh2StorCAPEX': 500, 'lh2StorOPEX': 2.5, 'lh2StorLife': 25, 'lh2Boiloff': 0.05,
        'lh2DispCAPEX': 750, 'lh2DispUtil': 70, 'lh2DispOPEX': 3.5, 'lh2DispLife': 12,
    },
    'moderat': {
        'label': 'Moderat',
        'elyCAPEX': 1000, 'elyOPEX': 3.0, 'elyLife': 20, 'elyEta': 58, 'elyWACC': 7.0,
        'compEta': 76, 'compCAPEX': 10000, 'compOPEX': 3.0, 'compLife': 15,
        'storCAPEX': 800, 'storOPEX': 2.0, 'storLife': 22,
        'dispCAPEX': 520, 'dispUtil': 80, 'dispOPEX': 2.5, 'dispLife': 15,
        'liqSEC': 13.0, 'liqCAPEX': 130_000, 'liqOPEX': 4.0, 'liqLife': 22,
        'gh2BufDays': 1.0, 'gh2BufCAPEX': 300, 'gh2BufOPEX': 2.0, 'gh2BufLife': 20,
        'lh2Days': 0.5, 'lh2StorCAPEX': 350, 'lh2StorOPEX': 2.0, 'lh2StorLife': 27, 'lh2Boiloff': 0.20,
        'lh2DispCAPEX': 560, 'lh2DispUtil': 80, 'lh2DispOPEX': 2.5, 'lh2DispLife': 15,
    },
    'optimistisch': {
        'label': 'Optimistisch',
        'elyCAPEX': 600, 'elyOPEX': 2.0, 'elyLife': 25, 'elyEta': 65, 'elyWACC': 5.0,
        'compEta': 82, 'compCAPEX': 10000, 'compOPEX': 2.0, 'compLife': 20,
        'storCAPEX': 550, 'storOPEX': 1.5, 'storLife': 25,
        'dispCAPEX': 350, 'dispUtil': 90, 'dispOPEX': 1.5, 'dispLife': 18,
        'liqSEC': 10.0, 'liqCAPEX': 80_000, 'liqOPEX': 3.0, 'liqLife': 25,
        'lh2Days': 0.5, 'lh2StorCAPEX': 220, 'lh2StorOPEX': 1.5, 'lh2StorLife': 30, 'lh2Boiloff': 0.10,
        'lh2DispCAPEX': 380, 'lh2DispUtil': 90, 'lh2DispOPEX': 1.5, 'lh2DispLife': 18,
    },
}

ELY_PRESETS: dict[str, dict] = {
    # PEM-spezifische Presets (Quelle: Fraunhofer ISE H2Giga, IEA, Hydrogen Council)
    'PEM Konservativ': {
        # Heute: CAPEX 1.200–1.500 €/kW, System-Level ~60–65 kWh/kg
        'power_mw': 5, 'capex_eur_kw': 1400, 'opex_pct': 3.0,
        'specific_kwh_kg': 62, 'flh': 3200, 'life_a': 20,
        'wacc_pct': 9.0, 'stack_life_h': 50_000, 'stack_pct': 40.0,
    },
    'PEM Moderat': {
        # Nahe Term 2027–2030: CAPEX ~900–1.100 €/kW, System-Level ~55–58 kWh/kg
        'power_mw': 5, 'capex_eur_kw': 1000, 'opex_pct': 3.0,
        'specific_kwh_kg': 57, 'flh': 4000, 'life_a': 20,
        'wacc_pct': 7.0, 'stack_life_h': 70_000, 'stack_pct': 35.0,
    },
    'PEM Optimistisch': {
        # Langfrist 2030+: CAPEX <600 €/kW (Hydrogen Council-Ziel), ~50–52 kWh/kg
        'power_mw': 5, 'capex_eur_kw': 600, 'opex_pct': 2.0,
        'specific_kwh_kg': 51, 'flh': 6500, 'life_a': 25,
        'wacc_pct': 5.0, 'stack_life_h': 90_000, 'stack_pct': 30.0,
    },
}

CUSTOM_DEFAULTS: dict = {
    'elecPrice': 0.095,   'flh': 4000,
    'elyCAPEX': 900,      'elyOPEX': 3.0,  'elyLife': 20,  'elyEta': 68,  'elyWACC': 7.0,
    'p1': 30,             'p2': 350,
    'compEta': 76,        'compCAPEX': 10000, 'compOPEX': 3.0, 'compLife': 15,
    'storDays': 0.5,      'storCAPEX': 850,   'storOPEX': 2.0, 'storLife': 20,
    'dispCAPEX': 520,     'dispCap': 476,     'dispUtil': 80,  'dispOPEX': 2.5, 'dispLife': 15,
    'liqSEC': 13.0,       'liqCAPEX': 130_000, 'liqOPEX': 4.5,  'liqLife': 20,
    'lh2Days': 0.5,       'lh2StorCAPEX': 400, 'lh2StorOPEX': 2.0, 'lh2StorLife': 25,
    'lh2Boiloff': 0.20,
    'lh2DispCAPEX': 560,  'lh2DispUtil': 80, 'lh2DispOPEX': 2.5, 'lh2DispLife': 15,
}

MODEL_PARAMETERS = {
    'wacc_pct': 6.0,
    'lifetime_ely_a': 20,
    'lifetime_refueling_a': 15,
    'opex_ely_pct_per_a': 3.0,
    'opex_refueling_pct_per_a': 2.5,
    'aux_pct': 3.0,
    'specific_energy_nominal_kwh_per_kg': 55.0,
    'availability_pct': 100.0,
    'stack_life_h': 80000,
    'stack_replace_pct': 25.0,
    'h2_storage_kg': 1800,
}

REFUELING_CAPEX_DEFAULTS = {25: 600_000, 35: 700_000, 50: 900_000, 90: 1_500_000}

FLEET_BASE: list[dict] = [
    {'type': 'ICE Diesel (Referenz)', 'price': 150_000, 'fuel': 'diesel',
     'cons_100km': 27.0, 'toll_km': 0.348, 'kasko': 0.74, 'liab': 2264.88,
     'taxes': 556, 'res': 0, 'subsidy_rate': 0},
    {'type': 'ICEV, CH2', 'price': 250_000, 'fuel': 'h2_ch2',
     'cons_100km': 9.2,  'toll_km': 0.348, 'kasko': 0.74, 'liab': 2264.88,
     'taxes': 0, 'res': 0, 'subsidy_rate': 0.8},
    {'type': 'FCEV, LH2', 'price': 350_000, 'fuel': 'h2_lh2',
     'cons_100km': 8.0,  'toll_km': 0.190, 'kasko': 0.74, 'liab': 2264.88,
     'taxes': 0, 'res': 0, 'subsidy_rate': 0.8},
    {'type': 'FCEV, CH2', 'price': 400_000, 'fuel': 'h2_ch2',
     'cons_100km': 8.0,  'toll_km': 0.190, 'kasko': 0.74, 'liab': 2264.88,
     'taxes': 0, 'res': 0, 'subsidy_rate': 0.8},
]


# =====================================================================
# MATHEMATISCHE GRUNDFUNKTIONEN
# =====================================================================

def crf(rate: float, years: float) -> float:
    """Capital Recovery Factor (Annuitätenfaktor)."""
    years = max(years, 1)
    if rate <= 1e-9:
        return 1.0 / years
    f = (1 + rate) ** years
    return (rate * f) / (f - 1)


def re_lcoe(capex: float, opex_pct: float, flh: float,
            wacc_pct: float, lifetime: float, deprecated: bool = False) -> float:
    """LCOE einer EE-Anlage [€/kWh].
    Bei abgeschrieben (deprecated=True) entfällt der CAPEX-Annuitätenanteil."""
    if flh < 1:
        return 0.0
    annual = capex * (opex_pct / 100)
    if not deprecated:
        annual += capex * crf(wacc_pct / 100, lifetime)
    return annual / flh


def h2_compress_kwh(p1: float, p2: float, eta: float) -> float:
    """Polytrope Verdichtungsarbeit für H₂ [kWh/kg].
    kappa=1.4, R(H₂)=4.124 kJ/(kg·K), T1=293.15 K."""
    if p1 <= 0 or p2 <= p1 or eta <= 0:
        return 0.0
    kappa, r_h2, t1 = 1.4, 4.124, 293.15
    ratio = mpow(p2 / p1, (kappa - 1) / kappa)
    w_kj = (1 / eta) * (kappa / (kappa - 1)) * r_h2 * t1 * (ratio - 1)
    return w_kj / 3600.0  # kJ → kWh


def refueling_capex(q_kgh: float, capex_ref: float = 900_000,
                    q_ref: float = 50, alpha: float = 0.65) -> float:
    """Skalierungs-CAPEX Vertankungsanlage (Power-Law).
    CAPEX(Q) = CAPEX_ref · (Q/Q_ref)^alpha."""
    return capex_ref * mpow(max(q_kgh, 0.1) / q_ref, alpha)


def refueling_cost_per_kg(q_kgh: float, utilization_pct: float = 80,
                          life_a: float = 15, wacc_pct: float = 7.0,
                          opex_pct: float = 2.5) -> float:
    """Annualisierte Vertankungskosten [€/kg H₂]."""
    capex = refueling_capex(q_kgh)
    crf_val = crf(wacc_pct / 100, life_a)
    annual_capex = capex * crf_val
    annual_opex = capex * (opex_pct / 100)
    annual_throughput = q_kgh * 24 * 365.25 * (utilization_pct / 100)
    return (annual_capex + annual_opex) / max(annual_throughput, 1e-9)


# =====================================================================
# INPUT-DATENKLASSEN
# =====================================================================

@dataclass
class EESource:
    on: bool = True
    tech: str = 'Photovoltaik'
    share: float = 50.0
    capex: float = 700
    opex: float = 1.5
    flh: float = 1050
    life: float = 25
    wacc: float = 6.0
    dep: bool = False


@dataclass
class EEInputs:
    """Inputs für den EE-Mix (Eigenstrom + Restbezug Netz)."""
    mode: str = 'Preset'  # 'Preset' | 'Custom'
    preset: str = '70% PV + 30% Wind Onshore'   # ← Moderat-Szenario-Repräsentativ
    grid_price_eur_kwh: float = 0.15             # ← typ. DE-Industriestrompreis
    sources: list[EESource] = field(default_factory=lambda: [
        EESource(True,  'Photovoltaik',  50, 700,  1.5, 1050, 25, 6, False),
        EESource(True,  'Wind Onshore',  50, 1400, 2.5, 2000, 20, 6, False),
        EESource(False, 'Wind Offshore', 0,  3000, 3.0, 4200, 25, 6, False),
        EESource(False, 'Wasserkraft',   0,  2500, 2.0, 4500, 40, 6, False),
    ])


@dataclass
class ElyInputs:
    """Inputs für den Elektrolyseur (PEM-Auslegung)."""
    power_mw: float = 5.0
    capex_eur_kw: float = 1000.0      # PEM moderat: 900–1.100 €/kW (2024–2027)
    opex_pct: float = 3.0
    avail_pct: float = 95.0
    aux_pct: float = 3.0
    flh: float = 4000
    life_a: float = 20
    wacc_pct: float = 7.0
    stack_life_h: float = 60_000      # PEM: 50.000–80.000 h (kürzer als AEL)
    stack_replace: bool = True
    stack_pct: float = 35.0           # PEM Stack-Ersatz: 30–40 % des CAPEX
    specific_kwh_kg: float = 57.0     # PEM System-Level: ~55–62 kWh/kg (η_LHV ≈ 58 %)
    use_efficiency: bool = False
    eta_lhv_pct: float = 58.0         # PEM System-Level typisch: 55–62 % LHV


@dataclass
class ProcInputs:
    """Inputs der Prozesskette (Kompression, Speicher, Vertankung, LH₂)."""
    # Kompression
    p1_bar: float = 30
    p2_bar: float = 350
    comp_eta: float = 0.76
    comp_capex_eur_kgh: float = 10_000
    comp_opex_pct: float = 3.0
    comp_life_a: float = 15
    comp_loss_pct: float = 1.0
    comp_mfr_auto: bool = True
    comp_mfr_manual: float = 25.0
    # Kompressor-Auslastungsziel: läuft länger als ELY (über GH₂-Puffer @30 bar gepuffert)
    # comp_mfr = ann_h2_kg / (8760 h × comp_util_pct/100)
    comp_util_target_pct: float = 70.0
    # CGH₂-Speicher
    stor_days: float = 2.0
    stor_capex_eur_kg: float = 800
    stor_opex_pct: float = 2.0
    stor_life_a: float = 22
    # GH₂-Hauptpuffer @30 bar
    gh2_buf_days: float = 1.0
    gh2_buf_capex_eur_kg: float = 300
    gh2_buf_opex_pct: float = 2.0
    gh2_buf_life_a: float = 20
    # CGH₂-Vertankung
    disp_capex_keur: float = 0.0       # 0 ⇒ aus Skalierungsformel
    disp_util_pct: float = 80.0
    disp_opex_pct: float = 2.5
    disp_life_a: float = 15
    disp_loss_pct: float = 2.0
    # Verflüssigung
    liq_on: bool = False
    liq_sec_kwh_kg: float = 13.0
    liq_capex_eur_kgh: float = 130_000
    liq_opex_pct: float = 4.0
    liq_life_a: float = 22
    liq_loss_pct: float = 1.0
    # Verflüssiger-Auslastungsziel: Anlage läuft länger als ELY (über GH₂-Puffer gepuffert)
    # liq_mfr = ann_h2_kg / (8760 h × liq_util_pct/100)  → kleinere, besser ausgelastete Anlage
    liq_util_target_pct: float = 70.0
    # LH₂-Speicher
    lh2_stor_days: float = 2.5
    lh2_stor_capex_eur_kg: float = 350
    lh2_stor_opex_pct: float = 2.0
    lh2_stor_life_a: float = 27
    lh2_boiloff_pct_per_day: float = 0.03
    # LH₂-Vertankung
    lh2_disp_capex_keur: float = 560
    lh2_disp_util_pct: float = 80
    lh2_disp_opex_pct: float = 2.5
    lh2_disp_life_a: float = 15
    lh2_disp_loss_pct: float = 5.0


@dataclass
class FleetInputs:
    annual_km: float = 120_000
    life_a: float = 5
    wacc_pct: float = 6.0
    toll_share: float = 0.80
    diesel_price_eur_l: float = 1.51
    ice_ref_price_eur: float = 150_000
    liq_cost_per_kg_eur: float = 2.10  # LH₂-Aufschlag (€/kg) ggü. CH₂


@dataclass
class ImportInputs:
    distance_km: float = 450
    transport_ct_per_kg_km: float = 0.667
    price_a_eur_kg: float = 4.90   # Marktbasiert


# =====================================================================
# OUTPUT-DATENKLASSEN
# =====================================================================

@dataclass
class EEBlendResult:
    blended_eur_kwh: float
    ee_only_eur_kwh: float
    ee_share_pct: float
    grid_share_pct: float
    grid_price_eur_kwh: float
    rows: list[dict] = field(default_factory=list)


@dataclass
class ElyResult:
    elec_kg: float
    capex_kg: float
    opex_kg: float
    stack_kg: float
    lcoh_ely: float
    ann_h2_kg: float
    spec_eff_kwh_kg: float
    spec_nom_kwh_kg: float
    flh_eff: float
    flh_nom: float
    ann_elec_kwh: float


@dataclass
class ProcResult:
    comp: float
    stor: float           # storBase + gh2BufCGH2 (CGH₂-Pfad)
    stor_base: float
    disp: float
    liq: float
    gh2_buf_cgh2: float   # Hauptpuffer-Anteil CGH₂
    gh2_buf_lh2: float    # Hauptpuffer-Anteil LH₂
    lh2_stor: float
    lh2_disp: float
    lh2_total: float
    total_cgh2: float     # comp + stor + disp
    comp_energy_kwh_kg: float
    mfr_kgh: float          # Kompressor-Auslegungsmassenstrom (auf Auslastungsziel skaliert)
    mfr_ely_peak: float     # ELY-Nennmassenstrom bei Vollast (Referenz)
    liq_mfr_kgh: float      # Verflüssiger-Auslegungsmassenstrom (auf Auslastungsziel skaliert)


# =====================================================================
# RECHENKERN
# =====================================================================

def compute_ee_blend(ee: EEInputs) -> EEBlendResult:
    """Strommix-Berechnung: gewichteter LCOE aus EE-Anlagen + Netzanteil."""
    sources: list[dict] = []
    if ee.mode == 'Preset':
        for tech, share in EE_MIX_PRESETS.get(ee.preset, []):
            d = EE_DEFAULTS[tech]
            sources.append({'label': tech, 'share': share, **d, 'wacc': 6, 'dep': False})
    else:
        for s in ee.sources:
            if s.on and s.share > 0:
                sources.append({'label': s.tech, 'share': s.share, 'capex': s.capex,
                                'opex': s.opex, 'flh': s.flh, 'life': s.life,
                                'wacc': s.wacc, 'dep': s.dep})

    share_sum = sum(s['share'] for s in sources)
    scale = 100 / share_sum if share_sum > 100 else 1.0
    ee_share = min(100, share_sum * scale)
    grid_share = 100 - ee_share

    rows = []
    for s in sources:
        share_eff = s['share'] * scale
        lcoe = re_lcoe(s['capex'], s['opex'], s['flh'], s['wacc'], s['life'], s['dep'])
        rows.append({**s, 'shareEff': share_eff, 'lcoe': lcoe})

    ee_only = (
        sum((r['shareEff'] / max(ee_share, 1e-9)) * r['lcoe'] for r in rows)
        if ee_share > 0 else 0.0
    )
    blended = (sum((r['shareEff'] / 100) * r['lcoe'] for r in rows)
               + (grid_share / 100) * ee.grid_price_eur_kwh)

    return EEBlendResult(blended, ee_only, ee_share, grid_share,
                         ee.grid_price_eur_kwh, rows)


def compute_ely(ely: ElyInputs, elec_price_eur_kwh: float) -> ElyResult:
    """Elektrolyseur-LCOH-Berechnung."""
    spec_nom = ely.specific_kwh_kg
    if ely.use_efficiency:
        eta = max(ely.eta_lhv_pct / 100, 1e-6)
        spec_nom = 33.33 / eta  # 33,33 kWh/kg LHV-Heizwert von H₂

    flh_eff = ely.flh * (ely.avail_pct / 100)
    spec_eff = spec_nom * (1 + ely.aux_pct / 100)
    power_kw = ely.power_mw * 1000
    total_capex = power_kw * ely.capex_eur_kw
    ann_h2_kg = (power_kw * flh_eff) / max(spec_eff, 1e-9)
    ann_elec = power_kw * flh_eff

    elec_kg = elec_price_eur_kwh * spec_eff
    capex_kg = (total_capex * crf(ely.wacc_pct / 100, ely.life_a)) / max(ann_h2_kg, 1e-9)
    opex_kg = (total_capex * (ely.opex_pct / 100)) / max(ann_h2_kg, 1e-9)
    stack_pct = ely.stack_pct if ely.stack_replace else 0
    stack_events = flh_eff / max(ely.stack_life_h, 1e-9)
    stack_kg = (total_capex * (stack_pct / 100) * stack_events) / max(ann_h2_kg, 1e-9)

    return ElyResult(
        elec_kg, capex_kg, opex_kg, stack_kg,
        lcoh_ely=elec_kg + capex_kg + opex_kg + stack_kg,
        ann_h2_kg=ann_h2_kg, spec_eff_kwh_kg=spec_eff, spec_nom_kwh_kg=spec_nom,
        flh_eff=flh_eff, flh_nom=ely.flh, ann_elec_kwh=ann_elec,
    )


def compute_proc_chain(proc: ProcInputs, ely_in: ElyInputs,
                       ee_blend: EEBlendResult,
                       active_strategy: Optional[str] = None) -> ProcResult:
    """Prozesskette: Kompression, Speicher, Vertankung, Verflüssigung."""
    ely = compute_ely(ely_in, ee_blend.blended_eur_kwh)
    wacc = ely_in.wacc_pct / 100
    ann_h = max(ely.ann_h2_kg, 1e-9)
    blended = ee_blend.blended_eur_kwh
    LHV = 33.3  # kWh/kg H₂

    # ── Kompression ────────────────────────────────────────────────
    # ELY-Nettostrom: ann_h2 / flh_eff — konsistent mit ann_h (inkl. Hilfsstrom).
    # Physikalische Untergrenze: Kompressor kann nicht weniger Stunden laufen als
    # der ELY produziert (sonst mfr_kgh > ELY-Peak, unrealistisch ohne Riesenpuffer).
    mfr_ely_peak = ely.ann_h2_kg / max(ely.flh_eff, 1e-9)
    ely_cap_factor_pct = ely.flh_eff / 8760.0 * 100.0
    if not proc.comp_mfr_auto:
        mfr_kgh = proc.comp_mfr_manual
    else:
        # Kompressor läuft entkoppelt vom ELY (GH₂-Puffer @30 bar als Puffer).
        # Ziel-Auslastung mind. = ELY-Kapazitätsfaktor, damit mfr_kgh ≤ mfr_ely_peak.
        comp_util_eff = max(proc.comp_util_target_pct, ely_cap_factor_pct)
        mfr_kgh = ann_h / max(8760.0 * comp_util_eff / 100.0, 1e-9)

    e_comp = h2_compress_kwh(proc.p1_bar, proc.p2_bar, proc.comp_eta)
    e_cost_c = e_comp * blended
    ann_cap_c = (mfr_kgh * proc.comp_capex_eur_kgh) * crf(wacc, proc.comp_life_a)
    ann_opx_c = (mfr_kgh * proc.comp_capex_eur_kgh) * (proc.comp_opex_pct / 100)
    comp_loss_cost = (proc.comp_loss_pct / 100) * blended * LHV
    comp = e_cost_c + (ann_cap_c + ann_opx_c) / ann_h + comp_loss_cost

    # ── CGH₂-Speicher ──────────────────────────────────────────────
    stor_kg = ann_h / 365.25 * proc.stor_days
    stor_base = (stor_kg * proc.stor_capex_eur_kg * crf(wacc, proc.stor_life_a)
                 + stor_kg * proc.stor_capex_eur_kg * (proc.stor_opex_pct / 100)) / ann_h

    # ── CGH₂-Vertankung ────────────────────────────────────────────
    strat = STRATEGY_PARAMS.get(active_strategy) if active_strategy else None
    disp_kgh = (strat.get('dispKgh') or strat.get('compKgh') or 50) if strat else 50
    cap_d_manual = proc.disp_capex_keur * 1000
    cap_d_scaled = refueling_capex(disp_kgh)
    cap_d = cap_d_manual if (cap_d_manual > 0 and not strat) else cap_d_scaled  # noqa: F841
    disp_base = refueling_cost_per_kg(disp_kgh, proc.disp_util_pct, proc.disp_life_a,
                                       ely_in.wacc_pct, proc.disp_opex_pct)
    disp_loss_cost = (proc.disp_loss_pct / 100) * blended * LHV
    disp = disp_base + disp_loss_cost

    # ── GH₂-Hauptpuffer @30 bar ────────────────────────────────────
    gh2_buf_vol_kg = ann_h / 8760 * proc.gh2_buf_days * 24
    gh2_buf_ann_cap = gh2_buf_vol_kg * proc.gh2_buf_capex_eur_kg * crf(wacc, proc.gh2_buf_life_a)
    gh2_buf_ann_op = gh2_buf_vol_kg * proc.gh2_buf_capex_eur_kg * (proc.gh2_buf_opex_pct / 100)
    gh2_buf_total = (gh2_buf_ann_cap + gh2_buf_ann_op) / max(ann_h, 1e-9)

    # ── Verflüssigung ──────────────────────────────────────────────
    # Gleiche Logik wie Kompressor: Ziel-Auslastung mind. = ELY-Kapazitätsfaktor,
    # damit liq_mfr_kgh ≤ mfr_ely_peak.
    liq_util_eff = max(proc.liq_util_target_pct, ely_cap_factor_pct)
    liq_mfr_kgh = ann_h / max(8760.0 * liq_util_eff / 100.0, 1e-9)
    e_l = proc.liq_sec_kwh_kg * blended
    ann_cap_l = (liq_mfr_kgh * proc.liq_capex_eur_kgh) * crf(wacc, proc.liq_life_a)
    ann_opx_l = (liq_mfr_kgh * proc.liq_capex_eur_kgh) * (proc.liq_opex_pct / 100)
    liq_loss_cost = (proc.liq_loss_pct / 100) * blended * LHV
    liq = (e_l + (ann_cap_l + ann_opx_l) / ann_h + liq_loss_cost) if proc.liq_on else 0.0

    # ── LH₂-Speicher (Boil-off) ────────────────────────────────────
    boil_frac = min(proc.lh2_stor_days * (proc.lh2_boiloff_pct_per_day / 100), 0.45)
    lh2_vol_kg = ann_h / 365.25 * proc.lh2_stor_days
    lh2_ann_cap = lh2_vol_kg * proc.lh2_stor_capex_eur_kg * crf(wacc, proc.lh2_stor_life_a)
    lh2_ann_op = lh2_vol_kg * proc.lh2_stor_capex_eur_kg * (proc.lh2_stor_opex_pct / 100)
    lh2_net_kg_a = ann_h * (1 - boil_frac) if proc.liq_on else 0
    lh2_stor = (lh2_ann_cap + lh2_ann_op) / max(lh2_net_kg_a, 1e-9) if proc.liq_on else 0.0

    # ── LH₂-Vertankung ─────────────────────────────────────────────
    cap_kgd = disp_kgh * 24
    lh2_cap_d = proc.lh2_disp_capex_keur * 1000
    lh2_ann_th_d = cap_kgd * 365.25 * (proc.lh2_disp_util_pct / 100)
    lh2_disp_base = (
        (lh2_cap_d * crf(wacc, proc.lh2_disp_life_a)
         + lh2_cap_d * (proc.lh2_disp_opex_pct / 100)) / max(lh2_ann_th_d, 1e-9)
    ) if proc.liq_on else 0.0
    lh2_disp_loss_cost = ((proc.lh2_disp_loss_pct / 100) * blended * LHV) if proc.liq_on else 0.0
    lh2_disp = lh2_disp_base + lh2_disp_loss_cost

    # Aufteilung GH₂-Hauptpuffer: bei LH₂ je 50%, sonst 100% CGH₂
    if proc.liq_on:
        gh2_buf_cgh2 = gh2_buf_total * 0.5
        gh2_buf_lh2 = gh2_buf_total * 0.5
    else:
        gh2_buf_cgh2 = gh2_buf_total
        gh2_buf_lh2 = 0.0

    stor_with_buf = stor_base + gh2_buf_cgh2
    lh2_total = gh2_buf_lh2 + liq + lh2_stor + lh2_disp
    total_cgh2 = comp + stor_with_buf + disp

    return ProcResult(
        comp=comp, stor=stor_with_buf, stor_base=stor_base, disp=disp,
        liq=liq, gh2_buf_cgh2=gh2_buf_cgh2, gh2_buf_lh2=gh2_buf_lh2,
        lh2_stor=lh2_stor, lh2_disp=lh2_disp, lh2_total=lh2_total,
        total_cgh2=total_cgh2,
        comp_energy_kwh_kg=e_comp,
        mfr_kgh=mfr_kgh, mfr_ely_peak=mfr_ely_peak, liq_mfr_kgh=liq_mfr_kgh,
    )


def compute_fleet(fleet: FleetInputs, lcoh_ch2_eur_kg: float) -> list[dict]:
    """TCO der Flottenfahrzeuge [€/km]."""
    h2_ch2 = lcoh_ch2_eur_kg
    h2_lh2 = lcoh_ch2_eur_kg + fleet.liq_cost_per_kg_eur
    crf_val = crf(fleet.wacc_pct / 100, fleet.life_a)
    out: list[dict] = []
    for v in FLEET_BASE:
        fuel_price = (fleet.diesel_price_eur_l if v['fuel'] == 'diesel'
                      else h2_ch2 if v['fuel'] == 'h2_ch2' else h2_lh2)
        fuel_cost = (fleet.annual_km / 100) * v['cons_100km'] * fuel_price
        toll_cost = fleet.annual_km * v['toll_km'] * fleet.toll_share
        insurance = v['price'] * (v['kasko'] / 100) + v['liab']
        subsidy = max(v['price'] - fleet.ice_ref_price_eur, 0) * v['subsidy_rate']
        net_invest = v['price'] - subsidy
        capex_ann = max(net_invest - v['res'], 0) * crf_val
        total_ann = capex_ann + fuel_cost + toll_cost + insurance + v['taxes']
        tco_pkm = total_ann / max(fleet.annual_km, 1)
        out.append({**v, 'fuelCost': fuel_cost, 'tollCost': toll_cost,
                    'insurance': insurance, 'subsidy': subsidy, 'netInvest': net_invest,
                    'capexAnn': capex_ann, 'totalAnn': total_ann, 'tcoPkm': tco_pkm})
    return out


def compute_import(imp: ImportInputs) -> dict:
    """NH₃-Import-Route: Basispreis + Transport."""
    transport = (imp.transport_ct_per_kg_km / 100) * imp.distance_km
    return {
        'transport_eur_kg': transport,
        'total_a_eur_kg': imp.price_a_eur_kg + transport,
        'price_a': imp.price_a_eur_kg,
        'distance_km': imp.distance_km,
    }


# =====================================================================
# KOMFORT: KOMPLETT-RECHNUNG
# =====================================================================

@dataclass
class FullResult:
    ee_blend: EEBlendResult
    ely: ElyResult
    proc: ProcResult
    lcoh_total_cgh2: float
    lcoh_total_lh2: float
    fleet: list[dict]
    import_: dict


def compute_all(
    ee: EEInputs = None,
    ely: ElyInputs = None,
    proc: ProcInputs = None,
    fleet: FleetInputs = None,
    imp: ImportInputs = None,
    active_strategy: Optional[str] = None,
    elec_price_override: Optional[float] = None,
) -> FullResult:
    """Komfortfunktion: berechnet das gesamte Modell in einem Aufruf."""
    ee = ee or EEInputs()
    ely = ely or ElyInputs()
    proc = proc or ProcInputs()
    fleet = fleet or FleetInputs()
    imp = imp or ImportInputs()

    ee_blend = compute_ee_blend(ee)
    if elec_price_override is not None:
        # Override – behalte Aufteilung bei, nutze aber Override für Folgerechnungen
        ee_blend = EEBlendResult(elec_price_override, ee_blend.ee_only_eur_kwh,
                                  ee_blend.ee_share_pct, ee_blend.grid_share_pct,
                                  ee_blend.grid_price_eur_kwh, ee_blend.rows)

    ely_res = compute_ely(ely, ee_blend.blended_eur_kwh)
    proc_res = compute_proc_chain(proc, ely, ee_blend, active_strategy)

    lcoh_cgh2 = ely_res.lcoh_ely + proc_res.total_cgh2
    lcoh_lh2 = ely_res.lcoh_ely + proc_res.lh2_total + proc_res.stor_base  # LH₂ ohne CGH₂-Vertankung

    fleet_res = compute_fleet(fleet, lcoh_cgh2)
    imp_res = compute_import(imp)

    return FullResult(ee_blend, ely_res, proc_res, lcoh_cgh2, lcoh_lh2, fleet_res, imp_res)


# =====================================================================
# SELBSTTEST
# =====================================================================

if __name__ == '__main__':
    res = compute_all()
    print(f"Strommix       : {res.ee_blend.blended_eur_kwh*100:6.2f} ct/kWh "
          f"(EE-Anteil {res.ee_blend.ee_share_pct:.0f}%)")
    print(f"Elektrolyseur  : {res.ely.lcoh_ely:6.3f} €/kg")
    print(f"  davon Strom  : {res.ely.elec_kg:6.3f} €/kg")
    print(f"  CAPEX        : {res.ely.capex_kg:6.3f} €/kg")
    print(f"  OPEX         : {res.ely.opex_kg:6.3f} €/kg")
    print(f"  Stack-Ersatz : {res.ely.stack_kg:6.3f} €/kg")
    print(f"Jahresprod.    : {res.ely.ann_h2_kg:,.0f} kg H₂/a")
    print(f"Prozesskette   : {res.proc.total_cgh2:6.3f} €/kg")
    print(f"  Kompression  : {res.proc.comp:6.3f} €/kg ({res.proc.comp_energy_kwh_kg:.2f} kWh/kg)")
    print(f"  Speicher     : {res.proc.stor:6.3f} €/kg")
    print(f"  Vertankung   : {res.proc.disp:6.3f} €/kg")
    print(f"LCOH (CGH₂)    : {res.lcoh_total_cgh2:6.3f} €/kg")
    print()
    print("Flotte TCO:")
    for v in res.fleet:
        print(f"  {v['type']:30s} {v['tcoPkm']:.3f} €/km")
    print()
    print(f"Import: {res.import_['total_a_eur_kg']:.2f} €/kg")
