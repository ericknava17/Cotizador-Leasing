# Lease Quote Builder v0.1

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
