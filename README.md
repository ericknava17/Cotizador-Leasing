# Lease Quote Builder v0.5

App de cotización de arrendamiento con UX por roles.

## Roles
- Asesor comercial: cotiza cliente, accesorios, condiciones comerciales y servicios incluidos.
- Finanzas / Pricing: administra fondeo, catálogos, TC, política y defaults operativos.

## Métricas compartidas
- TIR
- ROA
- Margen %

## Ejecutar
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Producción
Para operación multiusuario real conviene mover configuración e historial a Supabase.
