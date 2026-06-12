# Lease Quote Builder v0.2

Cotizador de arrendamiento separado de Credit Pulse Monitor.

## Incluye
- Cotización multi-plazo.
- Resumen cliente para firma.
- Métricas internas.
- Configuración editable y guardable:
  - tipo de cambio
  - catálogo de unidades
  - fondeadores
  - límites de rentabilidad por política
  - operativos default
- Historial local.

## Ejecutar
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Producción
En Streamlit Cloud, los cambios en archivos locales pueden reiniciarse al redeploy. Para producción multiusuario se recomienda migrar `data/settings.json` y `data/quotes.json` a Supabase.


## v0.2
- Agrega tabla de amortización por plazo.
- Calcula saldo cliente, interés, capital y saldo final.
- Calcula tabla espejo de fondeo: saldo fondeador, interés, capital y saldo final.
- Calcula flujo mensual de rentabilidad: renta cliente - renta fondeador - costos + salida final.
- Recalcula TIR, ROA, VPN 12% y breakeven desde la tabla de flujos.
- Permite ver amortización por plazo y descargar Excel con hojas por plazo.
