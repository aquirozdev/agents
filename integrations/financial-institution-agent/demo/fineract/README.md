# Apache Fineract como core demo opcional

Este directorio existe únicamente para probar la plantilla de agentes con un core abierto y reproducible. Fineract no define el contrato que consume Dify y no es requisito para desplegar la plantilla en una institución.

Para probar el flujo completo, levanta una instancia de Fineract y configura el
gateway con `BANKING_CONNECTOR=fineract`. El adaptador opcional vive en
`../../gateway/src/banking_gateway/fineract_connector.py` y solo expone consultas de
perfil, cuentas, movimientos, productos de ahorro/crédito y cronogramas. Las
solicitudes, pagos, tarjetas y oficinas permanecen deshabilitadas hasta que se
implemente el flujo institucional correspondiente.

Para cualquier cliente real, el adaptador Fineract se reemplaza por el conector
del core existente; Dify continúa consumiendo el mismo `openapi.yaml`.

Desde este directorio puedes levantar core y gateway juntos:

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.gateway.yaml \
  up --build
```

El gateway queda en `http://localhost:8080`; usa `local-demo-token` solo para
pruebas locales. Primero crea y carga en Fineract un cliente, una cuenta, un
producto y, si aplica, un crédito. Los identificadores canónicos de la demo
son los identificadores numéricos de Fineract.

Para crear de forma reproducible un cliente, un producto de ahorro y una
cuenta sintética, ejecuta desde este directorio:

```bash
./seed-demo.sh
```

El script es idempotente por `FINERACT_CLIENT_EXTERNAL_ID` y
`FINERACT_PRODUCT_SHORT_NAME`; requiere `curl` y `jq`, acepta las variables
`FINERACT_BASE_URL`, `FINERACT_USERNAME`, `FINERACT_PASSWORD` y
`FINERACT_TENANT`, e imprime el `FINERACT_DEMO_CLIENT_ID` que debe pasarse al
gateway. No crea créditos ni ejecuta depósitos.

La imagen oficial de desarrollo y sus perfiles de prueba no deben promocionarse directamente a producción. La instalación productiva debe fijar una versión aprobada, usar secretos externos, TLS válido, roles mínimos, auditoría, backups y hardening institucional.
