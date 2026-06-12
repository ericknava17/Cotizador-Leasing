
import io
import json
import math
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

APP_VERSION = "v0.1"
DATA_DIR = Path("data")
SETTINGS_PATH = DATA_DIR / "settings.json"
QUOTES_PATH = DATA_DIR / "quotes.json"

st.set_page_config(page_title="Lease Quote Builder", page_icon="🚚", layout="wide")

CSS = """
<style>
.block-container {padding-top:1.2rem; max-width:1450px;}
.hero {background:linear-gradient(135deg,#111827 0%,#1f2937 55%,#0f766e 100%); color:white; border-radius:22px; padding:26px 30px; margin-bottom:18px; box-shadow:0 18px 40px rgba(17,24,39,.16);}
.hero h1 {margin:0; font-size:2.05rem; line-height:1.1; font-weight:850; letter-spacing:-.035em;}
.hero p {margin:10px 0 0; color:rgba(255,255,255,.82); font-size:1.02rem;}
.pill {display:inline-flex; gap:7px; align-items:center; padding:6px 12px; border-radius:999px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); margin-top:16px; font-weight:700;}
.kpi {background:#fff; border:1px solid #e5e7eb; border-radius:16px; padding:16px 18px; box-shadow:0 1px 2px rgba(0,0,0,.04);}
.kpi-label {font-size:.76rem; color:#6b7280; font-weight:800; letter-spacing:.04em; text-transform:uppercase;}
.kpi-value {font-size:1.45rem; color:#111827; font-weight:850; margin-top:4px;}
.kpi-caption {font-size:.82rem; color:#6b7280; margin-top:2px;}
.ok {color:#047857; font-weight:800;}
.warn {color:#b45309; font-weight:800;}
.bad {color:#b91c1c; font-weight:800;}
.client-card {background:#f8fafc; border:1px solid #dbeafe; border-radius:18px; padding:20px 24px; margin:12px 0;}
.small {font-size:.84rem; color:#6b7280;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------
# Persistence
# ------------------------
def ensure_data():
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps(default_settings(), indent=2, ensure_ascii=False), encoding="utf-8")
    if not QUOTES_PATH.exists():
        QUOTES_PATH.write_text("[]", encoding="utf-8")

def default_settings() -> Dict[str, Any]:
    return {
        "fx_usd_mxn": 17.35,
        "iva_pct": 0.16,
        "catalog_units": [],
        "funding": [],
        "policy": {"margen_mensual_min_pct": 0.055, "roa_min_pct": 0.055, "tir_min_pct": 0.18, "enganche_min_pct": 0.0, "deposito_min_rentas": 1, "plazos_permitidos": [12,18,24,36,48,60], "plazos_default": [24,36,48]},
        "operational_defaults": {}
    }

def load_settings() -> Dict[str, Any]:
    ensure_data()
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))

def save_settings(settings: Dict[str, Any]):
    ensure_data()
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")

def load_quotes() -> List[Dict[str, Any]]:
    ensure_data()
    try:
        return json.loads(QUOTES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_quotes(quotes: List[Dict[str, Any]]):
    ensure_data()
    QUOTES_PATH.write_text(json.dumps(quotes, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------
# Finance engine
# ------------------------
def money(x):
    try:
        return "${:,.0f}".format(float(x))
    except Exception:
        return "$0"

def pct(x):
    try:
        return "{:.2%}".format(float(x))
    except Exception:
        return "N/D"

def pmt(rate_monthly: float, nper: int, pv: float, fv: float = 0.0) -> float:
    """Payment paid at period end. Returns positive required payment."""
    if nper <= 0:
        return 0.0
    if abs(rate_monthly) < 1e-12:
        return (pv - fv) / nper
    return (pv - fv / ((1 + rate_monthly) ** nper)) * rate_monthly / (1 - (1 + rate_monthly) ** (-nper))

def npv(rate: float, cfs: List[float]) -> float:
    return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cfs))

def irr(cfs: List[float]) -> float:
    """Robust bisection-ish IRR for monthly cash flows."""
    if not cfs or min(cfs) >= 0 or max(cfs) <= 0:
        return float("nan")
    low, high = -0.95, 5.0
    f_low, f_high = npv(low, cfs), npv(high, cfs)
    if f_low * f_high > 0:
        # scan intervals
        grid = np.linspace(-0.95, 5, 300)
        prev_r, prev_f = grid[0], npv(grid[0], cfs)
        for r in grid[1:]:
            f = npv(float(r), cfs)
            if prev_f * f <= 0:
                low, high = float(prev_r), float(r)
                break
            prev_r, prev_f = r, f
        else:
            return float("nan")
    for _ in range(100):
        mid = (low + high) / 2
        f_mid = npv(mid, cfs)
        if abs(f_mid) < 1e-7:
            return mid
        if npv(low, cfs) * f_mid <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2

def breakeven_month(cfs: List[float]) -> Any:
    cum = 0
    for i, cf in enumerate(cfs):
        cum += cf
        if i > 0 and cum >= 0:
            return i
    return "No alcanza"

def quote_one(inputs: Dict[str, Any], settings: Dict[str, Any], plazo: int) -> Dict[str, Any]:
    units = int(inputs["numero_equipos"])
    fx = float(inputs["tc_usd"])
    precio_usd = float(inputs["precio_usd_sin_iva"])
    accesorios_usd = float(inputs.get("accesorios_usd", 0) or 0)
    unit_value = (precio_usd + accesorios_usd) * fx
    total_asset = unit_value * units

    enganche_pct = float(inputs["enganche_pct"])
    enganche_unit = unit_value * enganche_pct
    pv_client = unit_value - enganche_unit
    residual_client_pct = float(inputs["residual_cliente_pct"])
    residual_client_unit = unit_value * residual_client_pct

    tasa_cliente = float(inputs["tasa_cliente_nominal_pct"])
    tasa_cliente_m = tasa_cliente / 12
    renta_financiera_unit = pmt(tasa_cliente_m, plazo, pv_client, residual_client_unit)

    # Operative revenue/cost components per unit/month
    gestoria_client = float(inputs.get("gestoria_precio_cliente", 0) or 0)
    seguro_client = float(inputs.get("seguro_precio_cliente", 0) or 0)
    mantenimiento_client = float(inputs.get("mantenimiento_precio_cliente", 0) or 0)
    telemetria_client = float(inputs.get("telemetria_precio_cliente", 0) or 0)
    movilidad_unit = gestoria_client + seguro_client + mantenimiento_client + telemetria_client
    renta_final_unit = renta_financiera_unit + movilidad_unit

    # Funding
    funding_rate = float(inputs["costo_fondeo_nominal_pct"])
    funding_m = funding_rate / 12
    residual_fondeador_pct = float(inputs["residual_fondeador_pct"])
    residual_fondeador_unit = unit_value * residual_fondeador_pct
    funding_pv = unit_value  # base conservative: financier funds asset value
    renta_fondeador_unit = pmt(funding_m, plazo, funding_pv, residual_fondeador_unit)

    # Costs per unit/month borne by company
    gestoria_cost = float(inputs.get("gestoria_costo_empresa", 0) or 0)
    seguro_cost = float(inputs.get("seguro_costo_empresa", 0) or 0)
    mantenimiento_cost = float(inputs.get("mantenimiento_costo_empresa", 0) or 0)
    telemetria_cost = float(inputs.get("telemetria_costo_empresa", 0) or 0)
    costos_operativos_unit = gestoria_cost + seguro_cost + mantenimiento_cost + telemetria_cost

    margen_mensual_unit = renta_final_unit - renta_fondeador_unit - costos_operativos_unit
    margen_mensual_total = margen_mensual_unit * units
    margen_pct = margen_mensual_unit / renta_final_unit if renta_final_unit else float("nan")
    margen_anual_total = margen_mensual_total * 12

    comision_apertura_pct = float(inputs["comision_apertura_pct"])
    comision_apertura_total = total_asset * comision_apertura_pct
    deposito_rentas = float(inputs["deposito_rentas"])
    deposito_total = renta_final_unit * units * deposito_rentas

    venta_cliente_pct = float(inputs["valor_venta_cliente_pct"])
    venta_cliente_unit = unit_value * venta_cliente_pct
    eol_margin_unit = max(0, venta_cliente_unit - residual_fondeador_unit)
    eol_margin_total = eol_margin_unit * units

    total_margin_flows = margen_mensual_total * plazo
    margen_total = total_margin_flows + eol_margin_total + comision_apertura_total
    roa = margen_anual_total / total_asset if total_asset else float("nan")
    spread = tasa_cliente - funding_rate

    # economic IRR from company perspective, per unit then replicated.
    cfs = []
    initial_cf = -unit_value + enganche_unit + (comision_apertura_total / units if units else 0) + (deposito_total / units if units else 0)
    cfs.append(initial_cf)
    for m in range(1, plazo + 1):
        cf = renta_final_unit - costos_operativos_unit - renta_fondeador_unit
        if m == plazo:
            cf += venta_cliente_unit - residual_fondeador_unit
        cfs.append(cf)
    monthly_irr = irr(cfs)
    annual_irr = (1 + monthly_irr) ** 12 - 1 if not math.isnan(monthly_irr) else float("nan")

    policy = settings["policy"]
    pass_margin = bool(margen_pct >= float(policy["margen_mensual_min_pct"]))
    pass_roa = bool(roa >= float(policy["roa_min_pct"]))
    pass_tir = bool((not math.isnan(annual_irr)) and annual_irr >= float(policy["tir_min_pct"]))

    return {
        "plazo": plazo,
        "numero_equipos": units,
        "valor_unidad_mxn": unit_value,
        "valor_activos_total": total_asset,
        "enganche_total": enganche_unit * units,
        "monto_financiado_total": pv_client * units,
        "renta_financiera_unit": renta_financiera_unit,
        "ingreso_movilidad_unit": movilidad_unit,
        "renta_final_unit": renta_final_unit,
        "renta_final_total": renta_final_unit * units,
        "renta_fondeador_unit": renta_fondeador_unit,
        "renta_fondeador_total": renta_fondeador_unit * units,
        "costos_operativos_unit": costos_operativos_unit,
        "costos_operativos_total": costos_operativos_unit * units,
        "margen_mensual_unit": margen_mensual_unit,
        "margen_mensual_total": margen_mensual_total,
        "margen_mensual_pct": margen_pct,
        "margen_anual_total": margen_anual_total,
        "margen_total_flujos": total_margin_flows,
        "valor_residual_cliente_total": residual_client_unit * units,
        "valor_residual_fondeador_total": residual_fondeador_unit * units,
        "valor_venta_cliente_total": venta_cliente_unit * units,
        "venta_activos_eol_margen_total": eol_margin_total,
        "margen_total_operacion": margen_total,
        "roa": roa,
        "tir_anual": annual_irr,
        "spread_tasas_nominal_bps": spread * 10000,
        "comision_apertura_total": comision_apertura_total,
        "deposito_garantia_total": deposito_total,
        "breakeven_mes": breakeven_month(cfs),
        "cumple_margen": pass_margin,
        "cumple_roa": pass_roa,
        "cumple_tir": pass_tir,
        "cumple_politica": pass_margin and pass_roa and pass_tir,
        "cashflows_unit": cfs,
    }

def client_summary_markdown(case: Dict[str, Any], quote_rows: pd.DataFrame) -> str:
    lines = [
        f"# Resumen de cotización para firma",
        "",
        f"**Cliente:** {case.get('cliente','')}",
        f"**RFC:** {case.get('rfc','')}",
        f"**Modelo:** {case.get('modelo','')}",
        f"**Número de equipos:** {case.get('numero_equipos','')}",
        f"**Fecha:** {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## Opciones de plazo",
        "",
        "| Plazo | Renta mensual por unidad | Renta mensual total | Depósito en garantía | Comisión apertura | Valor residual cliente |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in quote_rows.iterrows():
        lines.append(f"| {int(r['plazo'])} | {money(r['renta_final_unit'])} | {money(r['renta_final_total'])} | {money(r['deposito_garantia_total'])} | {money(r['comision_apertura_total'])} | {money(r['valor_residual_cliente_total'])} |")
    lines += [
        "",
        "## Condiciones generales",
        "- Cotización sujeta a autorización de crédito, validación documental, disponibilidad de unidades y condiciones finales del fondeador.",
        "- Los importes no incluyen IVA salvo que se indique expresamente.",
        "- La cotización podrá ajustarse ante cambios en tipo de cambio, valor de la unidad, costo de fondeo, seguros, gastos de gestoría o políticas internas.",
        "",
        "## Aceptación del cliente",
        "",
        "Nombre y firma: _______________________________",
        "",
        "Fecha: _______________________________________",
    ]
    return "\n".join(lines)


# ------------------------
# UI
# ------------------------
settings = load_settings()

st.markdown(f"""
<div class="hero">
  <h1>Lease Quote Builder</h1>
  <p>Cotizador de arrendamiento con corrida multi-plazo, resumen para cliente y métricas internas de rentabilidad.</p>
  <div class="pill">🚚 {APP_VERSION}</div>
</div>
""", unsafe_allow_html=True)

tabs = st.tabs(["1. Cotizar", "2. Resumen cliente", "3. Métricas internas", "4. Configuración", "5. Historial", "6. Metodología"])

if "last_quote" not in st.session_state:
    st.session_state["last_quote"] = None

with tabs[0]:
    st.subheader("Cotización rápida para asesor")
    st.caption("Flujo pensado para que el asesor capture lo mínimo, compare varios plazos en una sola corrida y entregue una propuesta clara al cliente.")

    catalog = pd.DataFrame(settings.get("catalog_units", []))
    funding = pd.DataFrame(settings.get("funding", []))
    policy = settings.get("policy", {})
    ops = settings.get("operational_defaults", {})

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        cliente = st.text_input("Cliente / Razón social", value="")
        rfc = st.text_input("RFC", value="")
        producto = st.selectbox("Tipo producto", ["Arr. Largo Plazo", "Arrendamiento puro", "Arrendamiento financiero"], index=0)
        moneda_cliente = st.selectbox("Moneda contrato cliente", ["MXN", "USD"], index=0)
    with c2:
        modelo = st.selectbox("Modelo", catalog["modelo"].tolist() if not catalog.empty else ["Unidad genérica"])
        selected_unit = catalog[catalog["modelo"] == modelo].iloc[0].to_dict() if not catalog.empty and modelo in catalog["modelo"].tolist() else {}
        numero_equipos = st.number_input("Número de equipos", min_value=1, value=5, step=1)
        tc_usd = st.number_input("T.C. USD", min_value=1.0, value=float(settings.get("fx_usd_mxn", 17.35)), step=0.05)
        precio_usd = st.number_input("Valor unidad sin IVA USD", min_value=0.0, value=float(selected_unit.get("precio_usd_sin_iva", 0) or 0), step=100.0)
        accesorios_usd = st.number_input("Equipo aliado y accesorios USD", min_value=0.0, value=0.0, step=100.0)
    with c3:
        plazos_permitidos = policy.get("plazos_permitidos", [12,18,24,36,48,60])
        plazos_default = [p for p in policy.get("plazos_default", [24,36,48]) if p in plazos_permitidos]
        plazos = st.multiselect("Plazos a cotizar", plazos_permitidos, default=plazos_default or [plazos_permitidos[0]])
        tasa_cliente = st.number_input("Tasa arrendamiento nominal anual", min_value=0.0, value=0.1553, step=0.005, format="%.4f")
        residual_cliente = st.number_input("Valor residual cliente (%)", min_value=0.0, value=float(selected_unit.get("residual_cliente_pct", 0.001) or 0.001), step=0.001, format="%.4f")
        valor_venta_cliente = st.number_input("Valor venta cliente / salida (%)", min_value=0.0, value=0.0443, step=0.005, format="%.4f")

    st.markdown("### Estructura y fondeo")
    e1, e2, e3, e4 = st.columns(4)
    with e1:
        enganche_pct = st.number_input("Enganche %", min_value=0.0, value=float(policy.get("enganche_min_pct", 0.0)), step=0.01, format="%.4f")
        comision_apertura_si = st.selectbox("Comisión apertura", ["Sí", "No"], index=0)
        comision_apertura_pct = st.number_input("Comisión apertura %", min_value=0.0, value=0.015 if comision_apertura_si == "Sí" else 0.0, step=0.001, format="%.4f")
    with e2:
        deposito_si = st.selectbox("Depósito garantía", ["Sí", "No"], index=0)
        deposito_rentas = st.number_input("# rentas depósito", min_value=0.0, value=2.0 if deposito_si == "Sí" else 0.0, step=0.5)
        renta_anticipada = st.number_input("# rentas anticipadas", min_value=0.0, value=0.0, step=0.5)
    with e3:
        inst = st.selectbox("Institución fondeadora", funding["institucion"].tolist() if not funding.empty else ["Fondeador genérico"])
        frow = funding[funding["institucion"] == inst].iloc[0].to_dict() if not funding.empty and inst in funding["institucion"].tolist() else {}
        costo_fondeo = st.number_input("Costo fondeo nominal anual", min_value=0.0, value=float(frow.get("costo_fondeo_nominal_pct", 0.12) or 0.12), step=0.005, format="%.4f")
        residual_fondeador = st.number_input("Valor residual fondeador (%)", min_value=0.0, value=float(selected_unit.get("residual_fondeador_pct", frow.get("opcion_compra_pct", 0.001)) or 0.001), step=0.001, format="%.4f")
    with e4:
        meses_gracia = st.number_input("Meses de gracia", min_value=0, value=0, step=1)
        ratificacion = st.number_input("Ratificación / gastos legales", min_value=0.0, value=float(frow.get("ratificacion_mxn", 0) or 0), step=500.0)
        st.caption("La gracia queda capturada para versión posterior de calendario; en v0.1 se cotiza el plazo base.")

    st.markdown("### Operativos incluidos en la renta")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        gestoria_prop = st.selectbox("Gestoría", ["Propio", "Cliente"], index=0 if ops.get("gestoria_propietario", "Propio") == "Propio" else 1)
        gestoria_costo = st.number_input("Costo gestoría mensual", min_value=0.0, value=float(ops.get("gestoria_costo_mxn", 0) or 0), step=50.0)
        gestoria_markup = st.number_input("Markup gestoría", min_value=0.0, value=float(ops.get("gestoria_markup_pct", 0) or 0), step=0.005, format="%.4f")
    with o2:
        seguro_prop = st.selectbox("Seguro", ["Propio", "Cliente"], index=0 if ops.get("seguro_propietario", "Cliente") == "Propio" else 1)
        seguro_anual = st.number_input("Costo anual seguro sin IVA", min_value=0.0, value=float(ops.get("seguro_anual_sin_iva_mxn", 0) or 0), step=1000.0)
        seguro_markup = st.number_input("Markup seguro", min_value=0.0, value=float(ops.get("seguro_markup_pct", 0) or 0), step=0.005, format="%.4f")
    with o3:
        mant_si = st.selectbox("Mantenimiento incluido", ["Sí", "No"], index=0 if ops.get("mantenimiento_incluido", "No") == "Sí" else 1)
        costo_km = st.number_input("Costo por km", min_value=0.0, value=float(ops.get("mantenimiento_costo_km", 0) or 0), step=0.1)
        km_mes = st.number_input("Km estimado mensual", min_value=0.0, value=float(ops.get("km_estimado_mes", 0) or 0), step=500.0)
        mant_markup = st.number_input("Markup mantenimiento", min_value=0.0, value=float(ops.get("mantenimiento_markup_pct", 0) or 0), step=0.005, format="%.4f")
    with o4:
        telemetria_prop = st.selectbox("Telemetría", ["Propio", "Cliente"], index=0 if ops.get("telemetria_propietario", "Propio") == "Propio" else 1)
        telemetria_cliente = st.number_input("Precio telemetría cliente", min_value=0.0, value=float(ops.get("telemetria_precio_cliente_mxn", 250) or 250), step=25.0)
        telemetria_costo = st.number_input("Costo mínimo telemetría", min_value=0.0, value=float(ops.get("telemetria_costo_minimo_mxn", 214.5) or 214.5), step=25.0)

    gestoria_precio_cliente = gestoria_costo * (1 + gestoria_markup) if gestoria_prop == "Propio" else 0
    gestoria_costo_empresa = gestoria_costo if gestoria_prop == "Propio" else 0
    seguro_costo_mensual = seguro_anual / 12
    seguro_precio_cliente = seguro_costo_mensual * (1 + seguro_markup) if seguro_prop == "Propio" else 0
    seguro_costo_empresa = seguro_costo_mensual if seguro_prop == "Propio" else 0
    mant_costo_mensual = costo_km * km_mes if mant_si == "Sí" else 0
    mantenimiento_precio_cliente = mant_costo_mensual * (1 + mant_markup) if mant_si == "Sí" else 0
    mantenimiento_costo_empresa = mant_costo_mensual if mant_si == "Sí" else 0
    telemetria_precio_cliente = telemetria_cliente if telemetria_prop == "Propio" else 0
    telemetria_costo_empresa = telemetria_costo if telemetria_prop == "Propio" else 0

    inputs = {
        "cliente": cliente, "rfc": rfc, "producto": producto, "moneda_cliente": moneda_cliente,
        "modelo": modelo, "numero_equipos": numero_equipos, "tc_usd": tc_usd, "precio_usd_sin_iva": precio_usd,
        "accesorios_usd": accesorios_usd, "tasa_cliente_nominal_pct": tasa_cliente, "residual_cliente_pct": residual_cliente,
        "valor_venta_cliente_pct": valor_venta_cliente, "enganche_pct": enganche_pct,
        "comision_apertura_pct": comision_apertura_pct if comision_apertura_si == "Sí" else 0,
        "deposito_rentas": deposito_rentas if deposito_si == "Sí" else 0,
        "costo_fondeo_nominal_pct": costo_fondeo, "residual_fondeador_pct": residual_fondeador,
        "gestoria_precio_cliente": gestoria_precio_cliente, "gestoria_costo_empresa": gestoria_costo_empresa,
        "seguro_precio_cliente": seguro_precio_cliente, "seguro_costo_empresa": seguro_costo_empresa,
        "mantenimiento_precio_cliente": mantenimiento_precio_cliente, "mantenimiento_costo_empresa": mantenimiento_costo_empresa,
        "telemetria_precio_cliente": telemetria_precio_cliente, "telemetria_costo_empresa": telemetria_costo_empresa,
        "ratificacion_mxn": ratificacion, "meses_gracia": meses_gracia, "rentas_anticipadas": renta_anticipada,
        "institucion_fondeadora": inst,
    }

    if st.button("Calcular cotización multi-plazo", type="primary", use_container_width=True):
        rows = [quote_one(inputs, settings, int(p)) for p in plazos]
        df = pd.DataFrame(rows)
        st.session_state["last_quote"] = {"case": inputs, "rows": df.to_dict(orient="records"), "created_at": datetime.now().isoformat()}
        st.success("Cotización calculada. Revisa Resumen cliente y Métricas internas.")

    if st.session_state["last_quote"]:
        df = pd.DataFrame(st.session_state["last_quote"]["rows"])
        st.markdown("### Opciones calculadas")
        view_cols = ["plazo","renta_final_unit","renta_final_total","deposito_garantia_total","comision_apertura_total","margen_mensual_pct","roa","tir_anual","cumple_politica"]
        st.dataframe(df[view_cols].style.format({
            "renta_final_unit":"${:,.0f}", "renta_final_total":"${:,.0f}", "deposito_garantia_total":"${:,.0f}",
            "comision_apertura_total":"${:,.0f}", "margen_mensual_pct":"{:.2%}", "roa":"{:.2%}", "tir_anual":"{:.2%}"
        }), use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Resumen para cliente")
    if not st.session_state["last_quote"]:
        st.info("Primero calcula una cotización.")
    else:
        case = st.session_state["last_quote"]["case"]
        df = pd.DataFrame(st.session_state["last_quote"]["rows"])
        st.markdown("<div class='client-card'>", unsafe_allow_html=True)
        st.markdown(f"### {case.get('cliente') or 'Cliente'}")
        st.write(f"**RFC:** {case.get('rfc','')}  \n**Unidad:** {case.get('modelo','')}  \n**Número de equipos:** {case.get('numero_equipos')}")
        client_cols = ["plazo","renta_final_unit","renta_final_total","deposito_garantia_total","comision_apertura_total","valor_residual_cliente_total"]
        st.dataframe(df[client_cols].rename(columns={
            "plazo":"Plazo", "renta_final_unit":"Renta mensual por unidad", "renta_final_total":"Renta mensual total",
            "deposito_garantia_total":"Depósito garantía", "comision_apertura_total":"Comisión apertura",
            "valor_residual_cliente_total":"Valor residual cliente"
        }).style.format({
            "Renta mensual por unidad":"${:,.0f}", "Renta mensual total":"${:,.0f}", "Depósito garantía":"${:,.0f}",
            "Comisión apertura":"${:,.0f}", "Valor residual cliente":"${:,.0f}"
        }), use_container_width=True, hide_index=True)
        st.caption("Cotización sujeta a autorización de crédito, validación documental, disponibilidad de unidades y condiciones finales del fondeador. Importes sin IVA salvo indicación contraria.")
        st.markdown("</div>", unsafe_allow_html=True)

        md = client_summary_markdown(case, df)
        st.download_button("Descargar resumen cliente (.md)", data=md.encode("utf-8"), file_name="resumen_cotizacion_cliente.md", mime="text/markdown", use_container_width=True)

with tabs[2]:
    st.subheader("Métricas internas")
    if not st.session_state["last_quote"]:
        st.info("Primero calcula una cotización.")
    else:
        df = pd.DataFrame(st.session_state["last_quote"]["rows"])
        best = df.sort_values(["cumple_politica","margen_total_operacion"], ascending=[False, False]).iloc[0]
        k1,k2,k3,k4,k5 = st.columns(5)
        k1.markdown(f"<div class='kpi'><div class='kpi-label'>Mejor plazo</div><div class='kpi-value'>{int(best['plazo'])} meses</div><div class='kpi-caption'>Por política + margen</div></div>", unsafe_allow_html=True)
        k2.markdown(f"<div class='kpi'><div class='kpi-label'>Margen mensual</div><div class='kpi-value'>{pct(best['margen_mensual_pct'])}</div><div class='kpi-caption'>{money(best['margen_mensual_total'])} total</div></div>", unsafe_allow_html=True)
        k3.markdown(f"<div class='kpi'><div class='kpi-label'>ROA</div><div class='kpi-value'>{pct(best['roa'])}</div><div class='kpi-caption'>Anualizado</div></div>", unsafe_allow_html=True)
        k4.markdown(f"<div class='kpi'><div class='kpi-label'>TIR</div><div class='kpi-value'>{pct(best['tir_anual'])}</div><div class='kpi-caption'>Económica anual</div></div>", unsafe_allow_html=True)
        k5.markdown(f"<div class='kpi'><div class='kpi-label'>Margen total</div><div class='kpi-value'>{money(best['margen_total_operacion'])}</div><div class='kpi-caption'>Flujos + EOL + comisión</div></div>", unsafe_allow_html=True)

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df["plazo"], y=df["margen_mensual_pct"], name="Margen mensual %"))
        fig.add_trace(go.Scatter(x=df["plazo"], y=df["roa"], mode="lines+markers", name="ROA"))
        fig.add_trace(go.Scatter(x=df["plazo"], y=df["tir_anual"], mode="lines+markers", name="TIR"))
        fig.update_layout(title="Comparativo interno por plazo", xaxis_title="Plazo", yaxis_tickformat=".1%", height=430)
        st.plotly_chart(fig, use_container_width=True)

        internal_cols = ["plazo","valor_activos_total","renta_financiera_unit","ingreso_movilidad_unit","renta_final_unit","renta_fondeador_unit","costos_operativos_unit","margen_mensual_unit","margen_mensual_pct","margen_total_flujos","venta_activos_eol_margen_total","margen_total_operacion","roa","tir_anual","spread_tasas_nominal_bps","breakeven_mes","cumple_margen","cumple_roa","cumple_tir","cumple_politica"]
        st.dataframe(df[internal_cols].style.format({
            "valor_activos_total":"${:,.0f}", "renta_financiera_unit":"${:,.0f}", "ingreso_movilidad_unit":"${:,.0f}", "renta_final_unit":"${:,.0f}",
            "renta_fondeador_unit":"${:,.0f}", "costos_operativos_unit":"${:,.0f}", "margen_mensual_unit":"${:,.0f}",
            "margen_mensual_pct":"{:.2%}", "margen_total_flujos":"${:,.0f}", "venta_activos_eol_margen_total":"${:,.0f}",
            "margen_total_operacion":"${:,.0f}", "roa":"{:.2%}", "tir_anual":"{:.2%}", "spread_tasas_nominal_bps":"{:,.0f}"
        }), use_container_width=True, hide_index=True)

        if st.button("Guardar cotización en historial", use_container_width=True):
            quotes = load_quotes()
            quotes.append(st.session_state["last_quote"])
            save_quotes(quotes)
            st.success("Cotización guardada.")

with tabs[3]:
    st.subheader("Configuración interna editable")
    st.caption("Estos parámetros viven fuera de la cotización para poder cambiar fondeo, TC, catálogo de unidades y límites de política sin tocar el código.")

    settings_edit = json.loads(json.dumps(settings))

    c1,c2,c3,c4 = st.columns(4)
    with c1:
        settings_edit["fx_usd_mxn"] = st.number_input("Tipo de cambio USD/MXN", value=float(settings_edit.get("fx_usd_mxn",17.35)), step=0.05)
    with c2:
        settings_edit["iva_pct"] = st.number_input("IVA", value=float(settings_edit.get("iva_pct",0.16)), step=0.01, format="%.4f")
    with c3:
        settings_edit["policy"]["margen_mensual_min_pct"] = st.number_input("Margen mensual mínimo %", value=float(settings_edit["policy"].get("margen_mensual_min_pct",0.055)), step=0.005, format="%.4f")
    with c4:
        settings_edit["policy"]["roa_min_pct"] = st.number_input("ROA mínimo %", value=float(settings_edit["policy"].get("roa_min_pct",0.055)), step=0.005, format="%.4f")

    st.markdown("### Catálogo de unidades")
    catalog_df = pd.DataFrame(settings_edit.get("catalog_units", []))
    catalog_df = st.data_editor(catalog_df, num_rows="dynamic", use_container_width=True, key="catalog_editor")
    st.markdown("### Fondeadores")
    funding_df = pd.DataFrame(settings_edit.get("funding", []))
    funding_df = st.data_editor(funding_df, num_rows="dynamic", use_container_width=True, key="funding_editor")

    st.markdown("### Operativos default")
    ops_df = pd.DataFrame([settings_edit.get("operational_defaults", {})]).T.reset_index()
    ops_df.columns = ["parametro", "valor"]
    ops_df = st.data_editor(ops_df, num_rows="dynamic", use_container_width=True, key="ops_editor")

    cc1,cc2,cc3 = st.columns(3)
    with cc1:
        if st.button("Guardar configuración", type="primary", use_container_width=True):
            settings_edit["catalog_units"] = catalog_df.to_dict(orient="records")
            settings_edit["funding"] = funding_df.to_dict(orient="records")
            settings_edit["operational_defaults"] = dict(zip(ops_df["parametro"], ops_df["valor"]))
            save_settings(settings_edit)
            st.success("Configuración guardada. Recarga la app para tomar defaults actualizados.")
    with cc2:
        st.download_button("Exportar configuración JSON", data=json.dumps(settings, indent=2, ensure_ascii=False).encode("utf-8"), file_name="lease_quote_settings.json", mime="application/json", use_container_width=True)
    with cc3:
        up = st.file_uploader("Importar JSON", type=["json"], label_visibility="collapsed")
        if up is not None:
            try:
                imported = json.loads(up.read().decode("utf-8"))
                save_settings(imported)
                st.success("Configuración importada. Recarga la app.")
            except Exception as e:
                st.error(f"No pude importar JSON: {e}")

with tabs[4]:
    st.subheader("Historial de cotizaciones")
    quotes = load_quotes()
    if not quotes:
        st.info("Aún no hay cotizaciones guardadas.")
    else:
        rows = []
        for q in quotes:
            case = q.get("case", {})
            quote_rows = q.get("rows", [])
            best = pd.DataFrame(quote_rows).sort_values("margen_total_operacion", ascending=False).iloc[0] if quote_rows else {}
            rows.append({
                "fecha": q.get("created_at",""),
                "cliente": case.get("cliente",""),
                "rfc": case.get("rfc",""),
                "modelo": case.get("modelo",""),
                "equipos": case.get("numero_equipos",""),
                "mejor_plazo": best.get("plazo",""),
                "renta_total": best.get("renta_final_total",""),
                "margen_total": best.get("margen_total_operacion","")
            })
        hist = pd.DataFrame(rows)
        st.dataframe(hist.style.format({"renta_total":"${:,.0f}","margen_total":"${:,.0f}"}), use_container_width=True, hide_index=True)
        st.download_button("Exportar historial JSON", data=json.dumps(quotes, indent=2, ensure_ascii=False).encode("utf-8"), file_name="lease_quotes_history.json", mime="application/json", use_container_width=True)

with tabs[5]:
    st.subheader("Metodología")
    st.markdown("""
El MVP replica la lógica del archivo base en una UX de asesor:

- **Inputs comerciales:** producto, moneda, modelo, número de equipos, precio USD, tipo de cambio, plazo, tasa, residual y enganche.
- **Estructura:** comisión por apertura, depósito, renta anticipada y gracia.
- **Operativos:** gestoría, seguro, mantenimiento y telemetría como ingresos de movilidad y/o costos.
- **Fondeo:** institución, costo nominal, residual fondeador y depósito financiero.
- **KPIs:** renta financiera, ingreso por movilidad, renta final, renta a fondeador, margen mensual, margen %, ROA, TIR económica, spread, margen de venta de activo al final y breakeven.

La versión v0.1 deja la configuración en `data/settings.json`. Para producción multiusuario conviene mover la configuración a Supabase con perfiles por rol: asesor, pricing y administrador.
""")
