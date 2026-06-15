
import io, json, math, zipfile
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

APP_VERSION='v0.5'
DATA_DIR=Path('data')
SETTINGS_PATH=DATA_DIR/'settings.json'
QUOTES_PATH=DATA_DIR/'quotes.json'
st.set_page_config(page_title='Lease Quote Builder', page_icon='🚚', layout='wide')

CSS='''<style>
.block-container {padding-top:1.15rem; max-width:1450px;}
.hero {background:linear-gradient(135deg,#111827 0%,#1f2937 55%,#0f766e 100%); color:white; border-radius:22px; padding:26px 30px; margin-bottom:16px; box-shadow:0 18px 40px rgba(17,24,39,.16);}
.hero h1 {margin:0; font-size:2.05rem; line-height:1.1; font-weight:850; letter-spacing:-.035em;}
.hero p {margin:10px 0 0; color:rgba(255,255,255,.82); font-size:1.02rem;}
.pill {display:inline-flex; gap:7px; align-items:center; padding:6px 12px; border-radius:999px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); margin-top:16px; font-weight:700;}
.kpi {background:#fff; border:1px solid #e5e7eb; border-radius:16px; padding:16px 18px; box-shadow:0 1px 2px rgba(0,0,0,.04);}
.kpi-label {font-size:.76rem; color:#6b7280; font-weight:800; letter-spacing:.04em; text-transform:uppercase;}
.kpi-value {font-size:1.45rem; color:#111827; font-weight:850; margin-top:4px;}
.kpi-caption {font-size:.82rem; color:#6b7280; margin-top:2px;}
.soft-note {background:#eff6ff; border:1px solid #bfdbfe; color:#075985; border-radius:14px; padding:12px 14px; margin:8px 0 16px;}
.role-note {background:#f8fafc; border:1px solid #e5e7eb; border-radius:14px; padding:11px 13px; color:#374151;}
.ok {color:#047857; font-weight:850;} .bad{color:#b91c1c; font-weight:850;} .warn{color:#b45309; font-weight:850;}
</style>'''
st.markdown(CSS, unsafe_allow_html=True)

def ensure_data():
    DATA_DIR.mkdir(exist_ok=True)
    if not SETTINGS_PATH.exists(): SETTINGS_PATH.write_text('{}', encoding='utf-8')
    if not QUOTES_PATH.exists(): QUOTES_PATH.write_text('[]', encoding='utf-8')

def load_settings():
    ensure_data(); return json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))

def save_settings(s):
    ensure_data(); SETTINGS_PATH.write_text(json.dumps(s,indent=2,ensure_ascii=False),encoding='utf-8')

def load_quotes():
    ensure_data()
    try: return json.loads(QUOTES_PATH.read_text(encoding='utf-8'))
    except Exception: return []

def save_quotes(q):
    ensure_data(); QUOTES_PATH.write_text(json.dumps(q,indent=2,ensure_ascii=False),encoding='utf-8')

def money(x):
    try: return '${:,.0f}'.format(float(x))
    except Exception: return '$0'

def pct(x):
    try: return '{:.2%}'.format(float(x))
    except Exception: return 'N/D'

def pmt(rate,nper,pv,fv=0.0):
    if nper<=0: return 0.0
    if abs(rate)<1e-12: return (pv-fv)/nper
    return (pv - fv/((1+rate)**nper))*rate/(1-(1+rate)**(-nper))

def npv(rate,cfs): return sum(cf/((1+rate)**i) for i,cf in enumerate(cfs))

def irr(cfs):
    if not cfs or min(cfs)>=0 or max(cfs)<=0: return float('nan')
    grid=np.linspace(-.95,5,350)
    prev_r, prev_f = grid[0], npv(float(grid[0]), cfs)
    for r in grid[1:]:
        f=npv(float(r), cfs)
        if prev_f*f<=0:
            lo,hi=float(prev_r),float(r)
            for _ in range(80):
                mid=(lo+hi)/2; fm=npv(mid,cfs)
                if abs(fm)<1e-7: return mid
                if npv(lo,cfs)*fm<=0: hi=mid
                else: lo=mid
            return (lo+hi)/2
        prev_r,prev_f=r,f
    return float('nan')

def calc_schedule(inp, settings, plazo):
    units=int(inp['numero_equipos']); fx=float(inp['tc_usd'])
    unit_value=(float(inp['precio_usd_sin_iva'])+float(inp.get('accesorios_usd',0)))*fx
    asset_total=unit_value*units
    pv_client=asset_total*(1-float(inp['enganche_pct']))
    residual_client=asset_total*float(inp['residual_cliente_pct'])
    residual_fund=asset_total*float(inp['residual_fondeador_pct'])
    sale_value=asset_total*float(inp['valor_venta_cliente_pct'])
    rate_client=float(inp['tasa_cliente_nominal_pct'])/12
    rate_fund=float(inp['costo_fondeo_nominal_pct'])/12
    aforo=float(inp.get('aforo_pct',1.0))
    funded=asset_total*aforo
    renta_fin=pmt(rate_client,plazo,pv_client,residual_client)
    renta_fund=pmt(rate_fund,plazo,funded,residual_fund)
    movilidad_unit=float(inp['gestoria_precio_cliente'])+float(inp['seguro_precio_cliente'])+float(inp['mantenimiento_precio_cliente'])+float(inp['telemetria_precio_cliente'])
    costos_unit=float(inp['gestoria_costo_empresa'])+float(inp['seguro_costo_empresa'])+float(inp['mantenimiento_costo_empresa'])+float(inp['telemetria_costo_empresa'])
    movilidad=movilidad_unit*units; costos=costos_unit*units
    comision=asset_total*float(inp['comision_apertura_pct'])
    enganche=asset_total*float(inp['enganche_pct'])
    deposit=(renta_fin+movilidad)*float(inp['deposito_rentas'])
    antic=(renta_fin+movilidad)*float(inp.get('rentas_anticipadas',0))
    saldo_cliente=pv_client; saldo_fund=funded
    rows=[]
    rows.append({'mes':0,'saldo_cliente_inicial':0,'renta_financiera_cliente':0,'interes_cliente':0,'capital_cliente':0,'saldo_cliente_final':saldo_cliente,'ingreso_movilidad':0,'renta_total_cliente':0,'saldo_fondeo_inicial':0,'desembolso_fondeador':funded,'renta_fondeador':0,'interes_fondeo':0,'capital_fondeo':0,'saldo_fondeo_final':saldo_fund,'costos_operativos':0,'comision_apertura':comision,'enganche_cliente':enganche,'deposito_garantia':deposit,'devolucion_deposito':0,'rentas_anticipadas':antic,'valor_venta_activo':0,'pago_residual_fondeador':0,'flujo_neto_empresa':-asset_total+funded+enganche+comision+deposit+antic,'flujo_contrato':-asset_total+enganche+comision+antic})
    for m in range(1,plazo+1):
        sci=saldo_cliente; ic=sci*rate_client; capc=renta_fin-ic
        if m==plazo: capc=max(0,sci-residual_client)
        saldo_cliente=max(residual_client,sci-capc)
        sfi=saldo_fund; iff=sfi*rate_fund; capf=renta_fund-iff
        if m==plazo: capf=max(0,sfi-residual_fund)
        saldo_fund=max(residual_fund,sfi-capf)
        venta=sale_value if m==plazo else 0
        pago_res=residual_fund if m==plazo else 0
        dev_dep=deposit if m==plazo else 0
        renta_total=renta_fin+movilidad
        flujo_emp=renta_total-renta_fund-costos+venta-pago_res-dev_dep
        flujo_con=renta_total-costos+venta
        rows.append({'mes':m,'saldo_cliente_inicial':sci,'renta_financiera_cliente':renta_fin,'interes_cliente':ic,'capital_cliente':capc,'saldo_cliente_final':saldo_cliente,'ingreso_movilidad':movilidad,'renta_total_cliente':renta_total,'saldo_fondeo_inicial':sfi,'desembolso_fondeador':0,'renta_fondeador':renta_fund,'interes_fondeo':iff,'capital_fondeo':capf,'saldo_fondeo_final':saldo_fund,'costos_operativos':costos,'comision_apertura':0,'enganche_cliente':0,'deposito_garantia':0,'devolucion_deposito':dev_dep,'rentas_anticipadas':0,'valor_venta_activo':venta,'pago_residual_fondeador':pago_res,'flujo_neto_empresa':flujo_emp,'flujo_contrato':flujo_con})
    df=pd.DataFrame(rows)
    df['flujo_acumulado_empresa']=df['flujo_neto_empresa'].cumsum()
    df['flujo_acumulado_contrato']=df['flujo_contrato'].cumsum()
    df['plazo']=plazo
    return df

def quote_one(inp, settings, plazo):
    sched=calc_schedule(inp, settings, plazo)
    first=sched.iloc[1] if len(sched)>1 else sched.iloc[0]
    units=int(inp['numero_equipos']); asset_total=(float(inp['precio_usd_sin_iva'])+float(inp.get('accesorios_usd',0)))*float(inp['tc_usd'])*units
    renta_final_total=float(first['renta_total_cliente']); renta_final_unit=renta_final_total/units
    renta_fund_unit=float(first['renta_fondeador'])/units
    costos_unit=float(first['costos_operativos'])/units
    margen_mensual_total=float(first['renta_total_cliente']-first['renta_fondeador']-first['costos_operativos'])
    margen_pct=(renta_final_unit-renta_fund_unit-costos_unit)/renta_final_unit if renta_final_unit else np.nan
    roa=margen_mensual_total*12/asset_total if asset_total else np.nan
    tir_contrato=(1+irr(sched['flujo_contrato'].tolist()))**12-1 if not math.isnan(irr(sched['flujo_contrato'].tolist())) else np.nan
    equity_irr=irr(sched['flujo_neto_empresa'].tolist())
    tir_equity=(1+equity_irr)**12-1 if not math.isnan(equity_irr) and sched['flujo_neto_empresa'].iloc[0] < 0 else np.nan
    vpn=npv(float(settings['policy'].get('vpn_discount_pct',.12))/12, sched['flujo_neto_empresa'].tolist())
    policy=settings['policy']
    return {'plazo':plazo,'renta_final_unit':renta_final_unit,'renta_final_total':renta_final_total,'total_mensual_con_iva':renta_final_total*(1+float(settings.get('iva_pct',.16))),'deposito_garantia_total':float(sched['deposito_garantia'].iloc[0]),'comision_apertura_total':float(sched['comision_apertura'].iloc[0]),'valor_residual_cliente_total':asset_total*float(inp['residual_cliente_pct']),'renta_fondeador_unit':renta_fund_unit,'costos_operativos_unit':costos_unit,'margen_mensual_total':margen_mensual_total,'margen_mensual_pct':margen_pct,'roa':roa,'tir':tir_contrato,'tir_equity':tir_equity,'vpn':vpn,'cumple_margen':margen_pct>=float(policy['margen_mensual_min_pct']),'cumple_roa':roa>=float(policy['roa_min_pct']),'cumple_tir':tir_contrato>=float(policy['tir_min_pct']) if not math.isnan(tir_contrato) else False,'cumple_politica':(margen_pct>=float(policy['margen_mensual_min_pct']) and roa>=float(policy['roa_min_pct']) and (tir_contrato>=float(policy['tir_min_pct']) if not math.isnan(tir_contrato) else False)),'schedule':sched.to_dict(orient='records')}

def pdf_cliente(case, rows, settings):
    bio=io.BytesIO(); doc=SimpleDocTemplate(bio,pagesize=letter,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.55*inch,bottomMargin=.55*inch)
    styles=getSampleStyleSheet(); title=ParagraphStyle('T', parent=styles['Title'], fontSize=18, leading=22, alignment=TA_CENTER, textColor=colors.HexColor('#111827'))
    h=ParagraphStyle('H', parent=styles['Heading2'], fontSize=11, leading=13, textColor=colors.HexColor('#0F766E'))
    body=ParagraphStyle('B', parent=styles['BodyText'], fontSize=8.8, leading=11)
    story=[Paragraph('Propuesta Comercial de Arrendamiento', title), Spacer(1,8), Paragraph(f"Cliente: <b>{case.get('cliente','')}</b> &nbsp;&nbsp; RFC: <b>{case.get('rfc','')}</b>", body), Paragraph(f"Unidad: <b>{case.get('modelo','')}</b> &nbsp;&nbsp; Equipos: <b>{case.get('numero_equipos')}</b> &nbsp;&nbsp; Producto: <b>{case.get('producto')}</b>", body), Spacer(1,8), HRFlowable(width='100%', thickness=.8, color=colors.HexColor('#CBD5E1')), Spacer(1,8), Paragraph('Opciones de plazo', h)]
    data=[['Plazo','Renta unidad','Renta total','Total c/IVA','Depósito','Comisión','Residual']]
    for r in rows:
        data.append([f"{int(r['plazo'])}m", money(r['renta_final_unit']), money(r['renta_final_total']), money(r['total_mensual_con_iva']), money(r['deposito_garantia_total']), money(r['comision_apertura_total']), money(r['valor_residual_cliente_total'])])
    t=Table(data, repeatRows=1, colWidths=[.55*inch,1.0*inch,1.0*inch,1.0*inch,1.0*inch,1.0*inch,1.0*inch])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#111827')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),('GRID',(0,0),(-1,-1),.35,colors.HexColor('#CBD5E1')),('ALIGN',(1,1),(-1,-1),'RIGHT'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F8FAFC')])]))
    story += [t, Spacer(1,10), Paragraph('Condiciones comerciales', h), Paragraph('Cotización sujeta a autorización de crédito, validación documental, disponibilidad de unidades y condiciones finales de fondeo. Importes sin IVA salvo donde se indique total con IVA. La propuesta podrá ajustarse por cambios en tipo de cambio, costo de fondeo, valor de unidad, seguros o políticas internas.', body), Spacer(1,20), Paragraph('Plazo elegido: ____________________ meses', body), Spacer(1,18), Paragraph('Nombre y firma del cliente: ____________________________________________', body), Spacer(1,14), Paragraph('Fecha: ____________________', body)]
    doc.build(story); bio.seek(0); return bio.getvalue()

settings=load_settings()
st.markdown(f"<div class='hero'><h1>Lease Quote Builder</h1><p>Cotizador por roles: asesor comercial y finanzas/pricing, con propuesta para cliente y métricas directas.</p><div class='pill'>🚚 {APP_VERSION}</div></div>", unsafe_allow_html=True)
with st.sidebar:
    st.caption(f'Versión {APP_VERSION}')
    role=st.radio('Tipo de usuario', ['Asesor comercial','Finanzas / Pricing'], index=0)
    st.markdown('<div class="role-note">Asesor captura cliente y condiciones elegibles por cliente. Finanzas administra fondeo, catálogos y políticas.</div>', unsafe_allow_html=True)

catalog=pd.DataFrame(settings.get('catalog_units',[])); catalog=catalog[catalog.get('activo',True)==True] if not catalog.empty and 'activo' in catalog.columns else catalog
funding=pd.DataFrame(settings.get('funding',[])); funding=funding[funding.get('activo',True)==True] if not funding.empty and 'activo' in funding.columns else funding
policy=settings.get('policy',{}); ops=settings.get('operational_defaults',{})

base_tabs=['Cotizar','Propuesta cliente','Métricas']
if role=='Finanzas / Pricing': base_tabs += ['Configuración','Historial','Metodología']
else: base_tabs += ['Metodología']
tabs=st.tabs(base_tabs)

if 'last_quote' not in st.session_state: st.session_state.last_quote=None

with tabs[0]:
    st.subheader('Cotización para asesor comercial')
    st.caption('Captura datos del cliente, unidad, accesorios, plazos y condiciones que puede elegir el cliente. Los costos de fondeo, catálogos y políticas vienen de Finanzas.')
    c1,c2,c3=st.columns(3)
    with c1:
        cliente=st.text_input('Cliente / Razón social')
        rfc=st.text_input('RFC')
        producto=st.selectbox('Producto', ['Arr. Largo Plazo','Arrendamiento puro','Arrendamiento financiero'])
        moneda=st.selectbox('Moneda contrato cliente', ['MXN','USD'])
    with c2:
        modelos=catalog['modelo'].tolist() if not catalog.empty else ['Unidad genérica']
        modelo=st.selectbox('Modelo', modelos)
        urow=catalog[catalog['modelo']==modelo].iloc[0].to_dict() if not catalog.empty and modelo in modelos else {}
        equipos=st.number_input('Número de equipos', min_value=1, value=1, step=1)
        tc=st.number_input('T.C. USD', min_value=1.0, value=float(settings.get('fx_usd_mxn',17.35)), step=.05)
        precio_usd=st.number_input('Valor unidad sin IVA USD', min_value=0.0, value=float(urow.get('precio_usd_sin_iva',0) or 0), step=100.0)
    with c3:
        plazos_permitidos=policy.get('plazos_permitidos',[12,18,24,36,48,60]); defaults=[p for p in policy.get('plazos_default',[36,48,60]) if p in plazos_permitidos]
        plazos=st.multiselect('Plazos a cotizar', plazos_permitidos, default=defaults or plazos_permitidos[:2])
        tasa_cliente=st.number_input('Tasa cliente nominal anual', min_value=0.0, value=0.1553, step=.005, format='%.4f')
        residual_cliente=st.number_input('Residual cliente %', min_value=0.0, value=float(urow.get('residual_cliente_pct',0.001) or 0.001), step=.001, format='%.4f')
        valor_salida=st.number_input('Valor venta/salida cliente %', min_value=0.0, value=float(urow.get('valor_venta_pct_default',0.0443) or .0443), step=.005, format='%.4f')

    st.markdown('### Accesorios incluidos')
    acc_default=pd.DataFrame([{'descripcion':'','monto_usd':0.0,'incluido':True}])
    acc=st.data_editor(acc_default, num_rows='dynamic', use_container_width=True, key='acc_editor')
    accesorios_usd=float(acc.loc[acc.get('incluido',True)==True,'monto_usd'].sum()) if not acc.empty and 'monto_usd' in acc.columns else 0.0

    st.markdown('### Condiciones comerciales del cliente')
    e1,e2,e3,e4=st.columns(4)
    default_funder=urow.get('fondeador_default','Traton')
    if default_funder not in funding['institucion'].tolist() if not funding.empty else []: default_funder=funding['institucion'].iloc[0] if not funding.empty else 'Fondeador genérico'
    frow=funding[funding['institucion']==default_funder].iloc[0].to_dict() if not funding.empty and default_funder in funding['institucion'].tolist() else {}
    with e1:
        enganche=st.number_input('Enganche %', min_value=0.0, value=float(policy.get('enganche_min_pct',0)), step=.01, format='%.4f')
        comision=st.number_input('Comisión apertura %', min_value=0.0, value=float(frow.get('comision_apertura_pct_default',0.015) or .015), step=.001, format='%.4f')
    with e2:
        deposito=st.number_input('# rentas depósito', min_value=0.0, value=float(frow.get('deposito_rentas_default',2) or 0), step=.5)
        rentas_ant=st.number_input('# rentas anticipadas', min_value=0.0, value=0.0, step=.5)
    with e3:
        st.metric('Fondeador ref. Finanzas', default_funder)
        st.metric('Costo fondeo ref.', pct(float(frow.get('costo_fondeo_nominal_pct',0))))
    with e4:
        st.metric('Aforo fondeador', pct(float(frow.get('aforo_pct',1))))
        st.metric('Residual fondeador ref.', pct(float(urow.get('residual_fondeador_pct', frow.get('residual_fondeador_pct_default',0.001)))))

    st.markdown('### Servicios / variables elegibles por cliente')
    o1,o2,o3,o4=st.columns(4)
    with o1:
        inc_gest=st.checkbox('Incluir gestoría', value=True)
        gest_costo=float(ops.get('gestoria_costo_mxn',0)) if inc_gest else 0
        gest_markup=float(ops.get('gestoria_markup_pct',0)) if inc_gest else 0
    with o2:
        inc_seg=st.checkbox('Incluir seguro', value=False)
        seguro_anual=st.number_input('Seguro anual sin IVA', min_value=0.0, value=float(ops.get('seguro_anual_sin_iva_mxn',0) or 0), step=1000.0, disabled=not inc_seg)
        seguro_markup=float(ops.get('seguro_markup_pct',0)) if inc_seg else 0
    with o3:
        inc_mant=st.checkbox('Incluir mantenimiento', value=False)
        km_mes=st.number_input('Km estimado mensual', min_value=0.0, value=float(ops.get('km_estimado_mes',0) or 0), step=500.0, disabled=not inc_mant)
        costo_km=float(ops.get('mantenimiento_costo_km',0)) if inc_mant else 0
        mant_markup=float(ops.get('mantenimiento_markup_pct',0)) if inc_mant else 0
    with o4:
        inc_tel=st.checkbox('Incluir telemetría', value=True)
        tel_precio=float(ops.get('telemetria_precio_cliente_mxn',250)) if inc_tel else 0
        tel_costo=float(ops.get('telemetria_costo_minimo_mxn',0)) if inc_tel else 0

    inp={'cliente':cliente,'rfc':rfc,'producto':producto,'moneda_cliente':moneda,'modelo':modelo,'numero_equipos':equipos,'tc_usd':tc,'precio_usd_sin_iva':precio_usd,'accesorios_usd':accesorios_usd,'tasa_cliente_nominal_pct':tasa_cliente,'residual_cliente_pct':residual_cliente,'valor_venta_cliente_pct':valor_salida,'enganche_pct':enganche,'comision_apertura_pct':comision,'deposito_rentas':deposito,'rentas_anticipadas':rentas_ant,'institucion_fondeadora':default_funder,'costo_fondeo_nominal_pct':float(frow.get('costo_fondeo_nominal_pct',0.12) or .12),'residual_fondeador_pct':float(urow.get('residual_fondeador_pct', frow.get('residual_fondeador_pct_default',0.001)) or .001),'aforo_pct':float(frow.get('aforo_pct',1) or 1),'gestoria_precio_cliente':gest_costo*(1+gest_markup),'gestoria_costo_empresa':gest_costo,'seguro_precio_cliente':seguro_anual/12*(1+seguro_markup) if inc_seg else 0,'seguro_costo_empresa':seguro_anual/12 if inc_seg else 0,'mantenimiento_precio_cliente':costo_km*km_mes*(1+mant_markup) if inc_mant else 0,'mantenimiento_costo_empresa':costo_km*km_mes if inc_mant else 0,'telemetria_precio_cliente':tel_precio,'telemetria_costo_empresa':tel_costo,'accesorios_detalle':acc.to_dict(orient='records') if not acc.empty else []}
    if st.button('Calcular cotización', type='primary', use_container_width=True, disabled=(not plazos)):
        rows=[quote_one(inp, settings, int(p)) for p in plazos]
        # strip schedules into separate dict for lighter table display
        schedules={int(r['plazo']):r.pop('schedule') for r in rows}
        st.session_state.last_quote={'case':inp,'rows':rows,'schedules':schedules,'created_at':datetime.now().isoformat()}
        st.success('Cotización calculada. Revisa Propuesta cliente y Métricas.')
    if st.session_state.last_quote:
        df=pd.DataFrame(st.session_state.last_quote['rows'])
        st.dataframe(df[['plazo','renta_final_unit','renta_final_total','total_mensual_con_iva','deposito_garantia_total','comision_apertura_total','margen_mensual_pct','roa','tir','cumple_politica']].style.format({'renta_final_unit':'${:,.0f}','renta_final_total':'${:,.0f}','total_mensual_con_iva':'${:,.0f}','deposito_garantia_total':'${:,.0f}','comision_apertura_total':'${:,.0f}','margen_mensual_pct':'{:.2%}','roa':'{:.2%}','tir':'{:.2%}'}), use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader('Propuesta comercial para cliente')
    if not st.session_state.last_quote: st.info('Primero calcula una cotización.')
    else:
        case=st.session_state.last_quote['case']; rows=st.session_state.last_quote['rows']; df=pd.DataFrame(rows)
        st.markdown(f"### {case.get('cliente') or 'Cliente'}")
        st.caption(f"RFC: {case.get('rfc','')} · Unidad: {case.get('modelo','')} · Equipos: {case.get('numero_equipos')} · Producto: {case.get('producto')}")
        st.dataframe(df[['plazo','renta_final_unit','renta_final_total','total_mensual_con_iva','deposito_garantia_total','comision_apertura_total','valor_residual_cliente_total']].rename(columns={'plazo':'Plazo','renta_final_unit':'Renta unidad','renta_final_total':'Renta total','total_mensual_con_iva':'Total c/IVA','deposito_garantia_total':'Depósito','comision_apertura_total':'Comisión','valor_residual_cliente_total':'Residual'}).style.format({'Renta unidad':'${:,.0f}','Renta total':'${:,.0f}','Total c/IVA':'${:,.0f}','Depósito':'${:,.0f}','Comisión':'${:,.0f}','Residual':'${:,.0f}'}), use_container_width=True, hide_index=True)
        st.download_button('Descargar PDF cliente', data=pdf_cliente(case, rows, settings), file_name='propuesta_cliente_arrendamiento.pdf', mime='application/pdf', use_container_width=True)

with tabs[2]:
    st.subheader('Métricas directas: TIR, ROA y Margen %')
    if not st.session_state.last_quote: st.info('Primero calcula una cotización.')
    else:
        df=pd.DataFrame(st.session_state.last_quote['rows'])
        best=df.sort_values(['cumple_politica','margen_mensual_pct'], ascending=[False,False]).iloc[0]
        a,b,c,d=st.columns(4)
        a.markdown(f"<div class='kpi'><div class='kpi-label'>Plazo referencia</div><div class='kpi-value'>{int(best['plazo'])}m</div><div class='kpi-caption'>Mejor opción por política/margen</div></div>", unsafe_allow_html=True)
        b.markdown(f"<div class='kpi'><div class='kpi-label'>TIR</div><div class='kpi-value'>{pct(best['tir'])}</div><div class='kpi-caption'>Contrato / activo</div></div>", unsafe_allow_html=True)
        c.markdown(f"<div class='kpi'><div class='kpi-label'>ROA</div><div class='kpi-value'>{pct(best['roa'])}</div><div class='kpi-caption'>Margen anual / activos</div></div>", unsafe_allow_html=True)
        d.markdown(f"<div class='kpi'><div class='kpi-label'>Margen %</div><div class='kpi-value'>{pct(best['margen_mensual_pct'])}</div><div class='kpi-caption'>Renta cliente - fondeo - costos</div></div>", unsafe_allow_html=True)
        fig=go.Figure(); fig.add_trace(go.Scatter(x=df['plazo'],y=df['tir'],mode='lines+markers',name='TIR')); fig.add_trace(go.Scatter(x=df['plazo'],y=df['roa'],mode='lines+markers',name='ROA')); fig.add_trace(go.Bar(x=df['plazo'],y=df['margen_mensual_pct'],name='Margen %'))
        fig.update_layout(yaxis_tickformat='.1%', title='Comparativo por plazo', height=430)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df[['plazo','tir','roa','margen_mensual_pct','margen_mensual_total','renta_final_total','cumple_margen','cumple_roa','cumple_tir','cumple_politica']].style.format({'tir':'{:.2%}','roa':'{:.2%}','margen_mensual_pct':'{:.2%}','margen_mensual_total':'${:,.0f}','renta_final_total':'${:,.0f}'}), use_container_width=True, hide_index=True)
        with st.expander('Ver tabla de amortización'):
            plazo_sel=st.selectbox('Plazo', sorted(st.session_state.last_quote['schedules'].keys()))
            sched=pd.DataFrame(st.session_state.last_quote['schedules'][plazo_sel])
            st.dataframe(sched, use_container_width=True, hide_index=True)
        if st.button('Guardar cotización en historial', use_container_width=True):
            q=load_quotes(); q.append(st.session_state.last_quote); save_quotes(q); st.success('Cotización guardada.')

if role=='Finanzas / Pricing':
    with tabs[3]:
        st.subheader('Configuración de Finanzas / Pricing')
        st.caption('Aquí se actualizan referencias internas: fondeo, TC, catálogos y política de rentabilidad. El asesor usa estos parámetros sin editarlos.')
        editable=json.loads(json.dumps(settings))
        c1,c2,c3,c4=st.columns(4)
        with c1: editable['fx_usd_mxn']=st.number_input('TC USD/MXN', value=float(editable.get('fx_usd_mxn',17.35)), step=.05)
        with c2: editable['iva_pct']=st.number_input('IVA', value=float(editable.get('iva_pct',.16)), step=.01, format='%.4f')
        with c3: editable['policy']['tir_min_pct']=st.number_input('TIR mínima', value=float(editable['policy'].get('tir_min_pct',.18)), step=.005, format='%.4f')
        with c4: editable['policy']['roa_min_pct']=st.number_input('ROA mínimo', value=float(editable['policy'].get('roa_min_pct',.055)), step=.005, format='%.4f')
        st.markdown('### Catálogo de unidades')
        cat_df=st.data_editor(pd.DataFrame(editable.get('catalog_units',[])), num_rows='dynamic', use_container_width=True)
        st.markdown('### Tipos de fondeo')
        fund_df=st.data_editor(pd.DataFrame(editable.get('funding',[])), num_rows='dynamic', use_container_width=True)
        st.markdown('### Defaults operativos')
        ops_df=pd.DataFrame([editable.get('operational_defaults',{})]).T.reset_index(); ops_df.columns=['parametro','valor']
        ops_df=st.data_editor(ops_df, num_rows='dynamic', use_container_width=True)
        if st.button('Guardar configuración interna', type='primary', use_container_width=True):
            editable['catalog_units']=cat_df.to_dict(orient='records'); editable['funding']=fund_df.to_dict(orient='records'); editable['operational_defaults']=dict(zip(ops_df['parametro'], ops_df['valor']))
            save_settings(editable); st.success('Configuración guardada. Recarga la app para tomar defaults actualizados.')
        st.download_button('Exportar configuración JSON', data=json.dumps(settings,indent=2,ensure_ascii=False).encode('utf-8'), file_name='lease_quote_settings.json', mime='application/json')
    with tabs[4]:
        st.subheader('Historial')
        q=load_quotes()
        if not q: st.info('Aún no hay cotizaciones guardadas.')
        else:
            rows=[]
            for x in q:
                case=x.get('case',{}); r=pd.DataFrame(x.get('rows',[]))
                best=r.sort_values('margen_mensual_pct', ascending=False).iloc[0].to_dict() if not r.empty else {}
                rows.append({'fecha':x.get('created_at',''),'cliente':case.get('cliente',''),'rfc':case.get('rfc',''),'modelo':case.get('modelo',''),'equipos':case.get('numero_equipos',''),'plazo_ref':best.get('plazo',''),'TIR':best.get('tir',np.nan),'ROA':best.get('roa',np.nan),'Margen%':best.get('margen_mensual_pct',np.nan)})
            st.dataframe(pd.DataFrame(rows).style.format({'TIR':'{:.2%}','ROA':'{:.2%}','Margen%':'{:.2%}'}), use_container_width=True, hide_index=True)

# methodology last tab depends index
with tabs[-1]:
    st.subheader('Metodología y roles')
    st.markdown('''
**Asesor comercial** captura sólo lo que corresponde al cliente: datos, unidad, accesorios, plazos, tasa, residual, enganche, comisión, depósito y servicios incluidos.  
**Finanzas / Pricing** administra los parámetros de referencia: fondeadores, costo de fondeo, aforo, residuales internos, tipo de cambio, catálogo de unidades y mínimos de rentabilidad.  
**Métricas compartidas:** ambos roles ven TIR, ROA y Margen %, de forma directa y comparable por plazo.

La app calcula amortización cliente/fondeador, flujos post-fondeo y flujos contrato. La **TIR** mostrada como métrica principal corresponde a la TIR del contrato/activo; la rentabilidad interna se complementa con ROA y Margen %.

Para producción multiusuario conviene migrar `data/settings.json` y `data/quotes.json` a Supabase, con permisos por rol.
''')
