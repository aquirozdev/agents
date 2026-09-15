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

El gateway queda en `http://localhost:8080` y usa identidad dinámica firmada
para resolver el cliente autenticado. Primero crea y carga en Fineract los
clientes, cuentas y productos. Los identificadores internos son numéricos,
pero el usuario se resuelve por el celular, correo o identificador externo y
no debe escribir un `customerId` para autenticarse.

Configura la aplicación Dify con `forward_end_user_identity=true` y el mismo
secreto de relay que usa el gateway. Para ensayar el acceso web abre
`http://localhost:8080/channels/auth`, verifica uno de los celulares cargados
y continúa al chat protegido.

Para crear de forma reproducible los clientes, un producto de ahorro y una
cuenta para cada cliente, ejecuta desde este directorio:

```bash
./seed-demo.sh
```

El script es idempotente por el identificador externo de cada cliente y por
`FINERACT_PRODUCT_SHORT_NAME`. Requiere `curl` y `jq`, acepta las variables
`FINERACT_BASE_URL`, `FINERACT_USERNAME`, `FINERACT_PASSWORD` y
`FINERACT_TENANT`, e imprime los identificadores de clientes y cuentas creados.
La lista se puede reemplazar con `FINERACT_CLIENTS`, usando el formato
`external-id|nombre|apellido|celular|correo`, separado por punto y coma; el
correo es opcional. No crea créditos ni ejecuta depósitos.

La imagen oficial de desarrollo y sus perfiles de prueba no deben promocionarse directamente a producción. La instalación productiva debe fijar una versión aprobada, usar secretos externos, TLS válido, roles mínimos, auditoría, backups y hardening institucional.
